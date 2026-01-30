from __future__ import annotations

import linecache
from concurrent.futures import Future
from functools import cache
from importlib.util import find_spec
from logging import getLogger
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pygments.style import Style
from pygments.styles import get_style_by_name
from pygments.token import Token
from PySide6.QtCore import QMimeData, QRect, QSize, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
    QWheelEvent,
)
from PySide6.QtWidgets import QDockWidget, QFileDialog, QHBoxLayout, QPlainTextEdit, QTextEdit, QVBoxLayout, QWidget

from ...api._helpers import output_metadata
from ...assets import IconName, IconReloadMixin
from ...vsenv import run_in_background, run_in_loop
from ..settings import ActionID, ShortcutManager
from .loader import VSEngineWorkspace

if TYPE_CHECKING:
    from pygments.style import Style
    from pygments.token import _TokenType

logger = getLogger(__name__)


class PygmentsHighlighter(QSyntaxHighlighter):
    """Syntax highlighter using Pygments for tokenization."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        from pygments.lexers.python import PythonLexer

        self.lexer = PythonLexer()
        self._formats: dict[_TokenType, QTextCharFormat] = {}
        self._style: type[Style]

    def highlightBlock(self, text: str) -> None:
        for index, token_type, value in self.lexer.get_tokens_unprocessed(text):
            token_t: _TokenType | None = token_type

            while token_t:
                if token_t in self._formats:
                    fmt = self._formats[token_t]
                    break
                token_t = token_t.parent
            else:
                fmt = QTextCharFormat()

            if fmt.isValid():
                self.setFormat(index, len(value), fmt)

    def set_style(self, style: type[Style]) -> None:
        self._style = style
        self._build_formats()
        self.rehighlight()

    def _build_formats(self) -> None:
        self._formats.clear()

        for token_type, style_dict in self._style:
            fmt = QTextCharFormat()

            if style_dict.get("color"):
                fmt.setForeground(QColor(f"#{style_dict['color']}"))
            if style_dict.get("bgcolor"):
                fmt.setBackground(QColor(f"#{style_dict['bgcolor']}"))
            if style_dict.get("bold"):
                fmt.setFontWeight(700)
            if style_dict.get("italic"):
                fmt.setFontItalic(True)
            if style_dict.get("underline"):
                fmt.setFontUnderline(True)

            self._formats[token_type] = fmt


class LineNumberArea(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event: QPaintEvent) -> None:
        self.editor.line_number_area_paint_event(event)

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width, 0)


class CodeEditor(QPlainTextEdit):
    MIN_FONT_SIZE = 8
    MAX_FONT_SIZE = 32
    DEFAULT_FONT_SIZE = 14

    fontSizeChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.font_size = self.DEFAULT_FONT_SIZE
        self.line_number_area = LineNumberArea(self)

        palette = self.palette()
        self._line_number_bg = palette.color(QPalette.ColorRole.Base)
        self._line_number_fg = palette.color(QPalette.ColorRole.PlaceholderText)
        self._separator_color = palette.color(QPalette.ColorRole.Mid)
        self._current_line_color = palette.color(QPalette.ColorRole.Highlight)
        self._current_line_color.setAlpha(40)

        # Connect signals for line number updates
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_line_number_area_width(0)
        self._highlight_current_line()

    @property
    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        # Left padding (3) + digit width + right padding (6) + separator (1)
        space = 3 + self.fontMetrics().horizontalAdvance("9") * max(digits, 2) + 6 + 1
        return space

    def insertFromMimeData(self, source: QMimeData) -> None:
        # Insert only plain text when pasting
        self.insertPlainText(source.text())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Tab:
            self.insertPlainText("    ")
        else:
            return super().keyPressEvent(event)

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width, cr.height()))

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()

            if delta > 0 and self.font_size < self.MAX_FONT_SIZE:
                self.font_size += 1
                self.fontSizeChanged.emit(self.font_size)
            elif delta < 0 and self.font_size > self.MIN_FONT_SIZE:
                self.font_size -= 1
                self.fontSizeChanged.emit(self.font_size)

            event.accept()
        else:
            super().wheelEvent(event)

    def set_theme_colors(
        self,
        line_number_bg: str,
        line_number_fg: str,
        current_line: str,
        separator_color: str,
    ) -> None:
        self._line_number_bg = QColor(line_number_bg)
        self._line_number_fg = QColor(line_number_fg)
        self._separator_color = QColor(separator_color)
        self._current_line_color = QColor(current_line)
        self._current_line_color.setAlpha(40)  # Subtle highlight
        self._highlight_current_line()
        self.line_number_area.update()

    def _update_line_number_area_width(self, _: int) -> None:
        self.setViewportMargins(self.line_number_area_width, 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def _highlight_current_line(self) -> None:
        extra_selections = list[QTextEdit.ExtraSelection]()

        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            # Missings stubs from PySide6
            selection.format.setBackground(self._current_line_color)  # type: ignore[attr-defined]
            selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)  # type: ignore[attr-defined]
            selection.cursor = self.textCursor()  # type: ignore[attr-defined]
            selection.cursor.clearSelection()  # type: ignore[attr-defined]
            extra_selections.append(selection)

        self.setExtraSelections(extra_selections)

    def line_number_area_paint_event(self, e: QPaintEvent) -> None:
        with QPainter(self.line_number_area) as painter:
            painter.fillRect(e.rect(), self._line_number_bg)
            # Draw separator line on the right edge
            separator_x = self.line_number_area.width() - 1
            painter.setPen(self._separator_color)
            painter.drawLine(separator_x, e.rect().top(), separator_x, e.rect().bottom())

            block = self.firstVisibleBlock()
            block_number = block.blockNumber()
            top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + round(self.blockBoundingRect(block).height())

            while block.isValid() and top <= e.rect().bottom():
                if block.isVisible() and bottom >= e.rect().top():
                    number = str(block_number + 1)
                    painter.setPen(self._line_number_fg)
                    painter.drawText(
                        0,
                        top,
                        self.line_number_area.width() - 6,  # Account for separator
                        self.fontMetrics().height(),
                        Qt.AlignmentFlag.AlignRight,
                        number,
                    )

                block = block.next()
                top = bottom
                bottom = top + round(self.blockBoundingRect(block).height())
                block_number += 1


def _darken_color(hex_color: str, factor: float) -> str:
    """Darken a hex color by a factor (0.0 = black, 1.0 = original)."""
    color = QColor(hex_color)
    return QColor.fromHslF(color.hslHueF(), color.hslSaturationF(), max(0, color.lightnessF() * factor)).name()


def _lighten_color(hex_color: str, factor: float) -> str:
    """Lighten a hex color by a factor (1.0 = original, 2.0 = brighter)."""
    color = QColor(hex_color)
    return QColor.fromHslF(color.hslHueF(), color.hslSaturationF(), min(1, color.lightnessF() * factor)).name()


def _dim_color(hex_color: str, factor: float) -> str:
    """Dim a color by reducing saturation and moving lightness toward middle gray."""
    color = QColor(hex_color)
    new_saturation = color.hslSaturationF() * factor
    new_lightness = color.lightnessF() * factor + 0.5 * (1 - factor)
    return QColor.fromHslF(color.hslHueF(), new_saturation, new_lightness).name()


class DockContainer(QWidget):
    def sizeHint(self) -> QSize:
        return QSize(400, 500)


class CodeEditorDock(QDockWidget, IconReloadMixin):
    ICON_SIZE = QSize(20, 20)
    ICON_COLOR = QPalette.ColorRole.ToolTipText

    runClicked = Signal()
    statusSavingScriptStarted = Signal(str)
    statusSavingScriptFinished = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Code Editor", parent)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        container = DockContainer(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar with Run button
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addStretch()

        self.run_btn = self.make_tool_button(
            IconName.PLAY,
            "Run script",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.run_btn.clicked.connect(self.runClicked.emit)
        toolbar_layout.addWidget(self.run_btn)

        self.save_btn = self.make_tool_button(
            IconName.SAVE,
            "Save script to file",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.save_btn.clicked.connect(self._on_save_clicked)
        toolbar_layout.addWidget(self.save_btn)

        layout.addWidget(toolbar)

        # Code editor
        self.editor = CodeEditor(self)
        self.editor.setPlainText(get_default_script())
        self.editor.fontSizeChanged.connect(self._apply_theme)
        self.highlighter = PygmentsHighlighter(self.editor.document())
        layout.addWidget(self.editor)

        self.setWidget(container)

        self._apply_theme()

        self._settings_manager.signals.globalChanged.connect(self._apply_theme)

    def _apply_theme(self) -> None:
        style = get_style_by_name(self._settings_manager.global_settings.appearance.editor_theme)

        # Get colors from Pygments style
        palette = self.editor.palette()
        bg_color = style.background_color or palette.color(QPalette.ColorRole.Base).name()
        # Derive contrasting fg if not provided: invert lightness for contrast
        bg_qcolor = QColor(bg_color)
        contrast_lightness = 0.15 if bg_qcolor.lightnessF() > 0.5 else 0.85
        default_fg = QColor.fromHslF(bg_qcolor.hslHueF(), 0.0, contrast_lightness).name()
        fg_color = style.style_for_token(Token).get("color") or default_fg
        fg_color = f"#{fg_color}" if not fg_color.startswith("#") else fg_color

        # Line number colors - use Pygments style if valid hex, otherwise derive from fg_color
        line_number_bg = getattr(style, "line_number_background_color", None)
        line_number_fg = getattr(style, "line_number_color", None)

        # Check if colors are valid hex (not 'inherit', 'transparent', etc.)
        line_bg = line_number_bg if line_number_bg and line_number_bg.startswith("#") else bg_color
        line_fg = line_number_fg if line_number_fg and line_number_fg.startswith("#") else _dim_color(fg_color, 0.6)
        current_line = style.highlight_color or _lighten_color(bg_color, 1.3)
        # Separator: lighten on dark backgrounds, darken on light backgrounds
        separator_color = _lighten_color(line_bg, 1.5) if bg_qcolor.lightnessF() < 0.5 else _darken_color(line_bg, 0.7)

        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {bg_color};
                color: {fg_color};
                font-family: 'Cascadia Mono', 'Consolas', 'Courier New', monospace;
                font-size: {self.editor.font_size}px;
                border: 1px solid {_lighten_color(bg_color, 1.5)};
                border-radius: 4px;
            }}
        """)
        self.editor.set_theme_colors(line_bg, line_fg, current_line, separator_color)
        self.highlighter.set_style(style)

    @Slot()
    def _on_save_clicked(self) -> None:
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Script",
            "",
            "VapourSynth Script (*.vpy);;All Files (*)",
        )
        if filepath:
            logger.debug("Saving script to %s", filepath)
            self._save_script(filepath, self.editor.toPlainText())

    @run_in_background(name="SaveScript")
    def _save_script(self, filepath: str, content: str) -> None:
        self.statusSavingScriptStarted.emit("Saving script...")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            self.statusSavingScriptFinished.emit("Saved")
        except Exception:
            logger.exception("Error saving script:")


class CodeContent:
    __slots__ = ("code", "filename")

    def __init__(self, code: str, filename: str) -> None:
        self.code = code
        self.filename = filename

    def splitlines(self, keepends: bool = False) -> list[str]:
        return self.code.splitlines(keepends)

    def __len__(self) -> int:
        return len(self.code.splitlines(keepends=False))

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return self.filename


class QuickScriptWorkspace(VSEngineWorkspace[CodeContent]):
    """Workspace for quick script editing and execution."""

    title = "Quick Script"
    icon = IconName.FILE_CODE

    content_type = "code"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filename = f"<vsview quickscript {uuid4().hex[:8].upper()}>"
        self.loaded_once = False

        self.tbar.setVisible(False)
        self.content_area.setVisible(False)

        self.code_dock = CodeEditorDock(self)
        self.code_dock.runClicked.connect(self._on_run_clicked)
        self.code_dock.statusSavingScriptStarted.connect(self.statusLoadingStarted.emit)
        self.code_dock.statusSavingScriptFinished.connect(self.statusLoadingFinished.emit)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.code_dock)

        self.stack.setCurrentWidget(self.loaded_page)

        ShortcutManager.register_shortcut(ActionID.RUN_QUICK_SCRIPT, self.code_dock.run_btn.click, self)

    @property
    def _script_content(self) -> Any:
        return self.content.code

    @property
    def _script_kwargs(self) -> dict[str, Any]:
        return {"filename": self.filename}

    def get_output_metadata(self) -> dict[int, str]:
        return output_metadata.get(self.content.filename, {})

    def loader(self) -> None:
        # Register source with linecache so traceback can display source lines for virtual files
        linecache.cache[self.filename] = (
            len(self.content),
            None,
            self.content.splitlines(keepends=True),
            self.filename,
        )

        return super().loader()

    def reload_content(self) -> Future[None]:
        self.content = CodeContent(self.code_dock.editor.toPlainText(), self.filename)

        if not self.loaded_once:
            return self.load_content(
                self.content,
                self.playback.state.current_frame,
                self.outputs_manager.current_video_index,
            )

        return super().reload_content()

    @run_in_loop(return_future=False)
    def set_loaded_page(self) -> None:
        self.content_area.setVisible(True)
        self.tbar.setVisible(True)
        self.stack.setCurrentWidget(self.loaded_page)

    @run_in_loop(return_future=False)
    def set_error_page(self) -> None:
        self.content_area.setVisible(False)
        self.tbar.setVisible(False)
        self.stack.setCurrentWidget(self.loaded_page)
        self.disable_reloading = False
        self.loaded_once = False  # Reset so next run does fresh load_content

    def _on_run_clicked(self) -> None:
        self.content = CodeContent(self.code_dock.editor.toPlainText(), self.filename)

        if not self.loaded_once:
            self.load_content(self.content)
            self.loaded_once = True  # Mark as loaded. Subsequent runs will reload
        else:
            self.reload_content()


@cache
def get_default_script() -> str:
    basic_import = (
        "from vstools import core, vs" if find_spec("vstools") else "import vapoursynth as vs\n\ncore = vs.core"
    )

    script = """
clip = core.std.BlankClip()
clip.set_output()
"""
    return basic_import + "\n\n" + script
