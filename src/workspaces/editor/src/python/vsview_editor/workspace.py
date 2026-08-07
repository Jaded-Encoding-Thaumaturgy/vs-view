from __future__ import annotations

import linecache
import shutil
import subprocess
from enum import StrEnum
from logging import getLogger
from pathlib import Path
from typing import Any, Literal, Self, override
from uuid import uuid4

from jetpytools import SPath, fallback
from PySide6.QtCore import QSize, Qt, QUrl, QUrlQuery, Signal, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from vsengine import ManagedEnvironment, UnifiedFuture

from vsview.api import (
    ActionDefinition,
    IconName,
    IconReloadMixin,
    PluginAPI,
    PluginSettings,
    run_in_background,
    run_in_loop,
)
from vsview.api._helpers import output_metadata
from vsview.api.workspace import PluginWorkspace
from vsview.app.settings import SettingsManager
from vsview.app.workspace import BaseGenericFileWorkspace, VSEngineWorkspace, get_default_script
from vsview.vsenv import QtEventLoop, create_environment

from .console import GlobalConsoleHub
from .lsp import LSP_BASEDPYRIGHT_CONFIG, LSPProcessManager
from .settings import EditorGlobalSettings
from .stubs import get_stubs_dir
from .utils import ContentPath
from .web import MonacoEditorWidget

logger = getLogger(__name__)


class MonacoEditorDock(QDockWidget, IconReloadMixin):
    # Resolve the dist directory relative to this module
    WEB_DIST_DIR = Path(__file__).parent / "web_dist"
    INDEX_PATH = WEB_DIST_DIR / "index.html"

    ICON_SIZE = QSize(22, 22)
    ICON_COLOR = QPalette.ColorRole.ToolTipText

    runClicked = Signal()
    activeFileChanged = Signal(object)  # Unused
    mainFileChanged = Signal(object)
    statusSavingScriptStarted = Signal(str)
    statusSavingScriptFinished = Signal(str)

    def __init__(
        self,
        parent: QWidget,
        loop: QtEventLoop,
        api: PluginAPI,
        settings: PluginSettings[EditorGlobalSettings, None],
    ) -> None:
        super().__init__("Code Editor", parent)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.loop = loop
        self.api = api
        self.settings = settings

        self._active_filepath: Path | None = None
        self._active_tab_uri: str | None = None
        self._main_filepath: Path | None = None
        self._main_tab_uri: str | None = None
        self._default_content = get_default_script()
        self._initial_content_set = False

        self.editor = MonacoEditorWidget(self)
        self.editor.bridge.editorReady.connect(self._on_editor_ready)
        self.editor.bridge.cursorPositionChanged.connect(self._on_cursor_changed)
        self.editor.bridge.activeTabChanged.connect(lambda uri: self._on_tab_changed(uri, "active"))
        self.editor.bridge.mainTabChanged.connect(lambda uri: self._on_tab_changed(uri, "main"))
        self.editor.bridge.saveRequested.connect(self._on_save_clicked)
        self.editor.bridge.saveAsRequested.connect(self._on_save_as_clicked)
        self.editor.bridge.formatRequested.connect(self._on_format_clicked)
        self.editor.bridge.generateStubsRequested.connect(lambda: self._generate_stubs(force=True))
        self.editor.bridge.consoleResized.connect(self._on_console_resized)

        query = QUrlQuery()
        query.addQueryItem("initialTheme", QUrl.toPercentEncoding(self.settings.global_.options.theme).toStdString())
        query.addQueryItem("windowColorRole", self.palette().color(QPalette.ColorRole.Window).name())
        query.addQueryItem("windowTextRole", self.palette().color(QPalette.ColorRole.WindowText).name())
        query.addQueryItem("accentColor", self.palette().color(QPalette.ColorRole.Accent).name())
        url = QUrl.fromLocalFile(MonacoEditorDock.INDEX_PATH)
        url.setQuery(query)
        self.editor.load(url)

        # Initialize LSP Process Manager
        self.lsp_manager = LSPProcessManager(self)

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.toolbar = QToolBar(self, movable=False)

        # File Group
        self.new_btn = self.make_tool_button(
            IconName.FILE_PLUS,
            "New Script",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.new_btn.clicked.connect(self._on_new_clicked)

        self.open_btn = self.make_tool_button(
            IconName.FILE_IMPORT,
            "Open Script",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.open_btn.clicked.connect(self._on_open_clicked)

        self.save_btn = self.make_tool_button(
            IconName.SAVE,
            "Save Script",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.save_btn.clicked.connect(self.editor.bridge.trigger_save)

        self.save_as_btn = self.make_tool_button(
            IconName.FILE_EXPORT,
            "Save Script As...",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.save_as_btn.clicked.connect(self.editor.bridge.trigger_save_as)

        # Code Tools Group
        self.format_btn = self.make_tool_button(
            IconName.BRACKETS_ANGLE,
            "Format Code (Ruff)",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.format_btn.clicked.connect(self.editor.bridge.trigger_format)

        self.wrap_btn = self.make_tool_button(
            IconName.TEXT_ALIGN_LEFT,
            "Toggle Word Wrap",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.wrap_btn.clicked.connect(self.editor.bridge.toggle_word_wrap)

        self.console_btn = self.make_tool_button(
            IconName.TERMINAL,
            "Toggle Console",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.console_btn.clicked.connect(self.editor.bridge.toggle_console)

        # Execution Group
        self.run_btn = self.make_tool_button(
            IconName.PLAY,
            "Run script",
            self,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.run_btn.clicked.connect(self.runClicked.emit)

        self.toolbar.addWidget(self.new_btn)
        self.toolbar.addWidget(self.open_btn)
        self.toolbar.addWidget(self.save_btn)
        self.toolbar.addWidget(self.save_as_btn)

        spacer1 = QWidget(self.toolbar)
        spacer1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer1)

        self.toolbar.addWidget(self.format_btn)
        self.toolbar.addWidget(self.wrap_btn)
        self.toolbar.addWidget(self.console_btn)

        spacer2 = QWidget(self.toolbar)
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer2)

        self.toolbar.addWidget(self.run_btn)

        layout.addWidget(self.toolbar, 0)
        layout.addWidget(self.editor, 1)

        status_bar = QWidget(self)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(4, 2, 4, 2)
        status_layout.addStretch()

        self.cursor_label = QLabel("Ln 1, Col 1", status_bar)
        self.cursor_label.setStyleSheet("color: gray; font-size: 11px;")
        status_layout.addWidget(self.cursor_label)

        layout.addWidget(status_bar, 0)
        self.setWidget(container)

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        self.api.register_shortcut(EditorShortcut.NEW_SCRIPT.definition, self.new_btn.click, self)
        self.api.register_shortcut(EditorShortcut.OPEN_SCRIPT.definition, self.open_btn.click, self)
        self.api.register_shortcut(EditorShortcut.SAVE_SCRIPT.definition, self.editor.bridge.trigger_save, self)
        self.api.register_shortcut(EditorShortcut.SAVE_SCRIPT_AS.definition, self.editor.bridge.trigger_save_as, self)
        self.api.register_shortcut(EditorShortcut.FORMAT_DOCUMENT.definition, self.editor.bridge.trigger_format, self)
        self.api.register_shortcut(EditorShortcut.TOGGLE_WORD_WRAP.definition, self.wrap_btn.click, self)
        self.api.register_shortcut(EditorShortcut.TOGGLE_CONSOLE.definition, self.console_btn.click, self)
        self.api.register_shortcut(EditorShortcut.RUN_SCRIPT.definition, self.run_btn.click, self)
        self.api.register_shortcut(
            EditorShortcut.GENERATE_STUBS.definition, lambda: self._generate_stubs(force=True), self
        )

    @override
    def deleteLater(self) -> None:
        GlobalConsoleHub.unregister(self.editor.bridge)
        self._env_stubs.dispose()
        return super().deleteLater()

    @property
    def main_filepath(self) -> Path | None:
        return self._main_filepath

    @property
    def _env_stubs(self) -> ManagedEnvironment:
        if not hasattr(self, "_env_stubs_internal"):
            self._env_stubs_internal = create_environment()
        return self._env_stubs_internal

    @Slot()
    def _on_editor_ready(self) -> None:
        """Initialize script and start LSP bridge when Monaco is ready."""
        if not self._initial_content_set:
            self.editor.bridge.open_tab(
                "file:///workspace/script.py",
                self._default_content,
                language="python",
                is_main=True,
            )
            self._initial_content_set = True
            logger.debug("Default script loaded into Monaco editor")

        self._on_settings_changed()
        self._generate_stubs(force=False)

        # Launch LSP process & server
        script_dir = self._active_filepath.parent if self._active_filepath else Path.cwd()
        port = self.lsp_manager.start_server(config=LSP_BASEDPYRIGHT_CONFIG, workspace_dir=script_dir)
        if port > 0:
            self.editor.bridge.connect_lsp(port, LSP_BASEDPYRIGHT_CONFIG)

    @Slot(int, int)
    def _on_cursor_changed(self, line: int, col: int) -> None:
        self.cursor_label.setText(f"Ln {line}, Col {col}")

    def _on_tab_changed(self, uri: str, kind: Literal["active", "main"]) -> None:
        setattr(self, f"_{kind}_tab_uri", uri or None)
        if not uri:
            setattr(self, f"_{kind}_filepath", None)
        else:
            url = QUrl(uri)
            local_path = url.toLocalFile()
            if url.scheme() == "file" and not local_path.startswith(("/workspace", "\\workspace")):
                setattr(self, f"_{kind}_filepath", Path(local_path))
            else:
                setattr(self, f"_{kind}_filepath", None)
        getattr(self, f"{kind}FileChanged").emit(getattr(self, f"_{kind}_filepath"))

    @Slot()
    def _on_save_clicked(self) -> None:
        if self._active_filepath:
            self._save_script(self._active_filepath, self.editor.bridge.content)
        else:
            self._on_save_as_clicked()

    @Slot()
    def _on_save_as_clicked(self) -> None:
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save Script",
            "",
            "VapourSynth Script (*.vpy);;All Files (*)",
        )
        if filepath:
            self._active_filepath = Path(filepath)
            self._save_script(self._active_filepath, self.editor.bridge.content)

    @Slot()
    def _on_format_clicked(self) -> None:
        if not (ruff_binary := shutil.which("ruff")):
            logger.error("ruff executable not found")
            return

        code = self.editor.bridge.content
        try:
            res = subprocess.run(
                [ruff_binary, "format", "-"],
                input=code,
                text=True,
                capture_output=True,
                check=True,
            )
            if res.stdout and res.stdout != code:
                self.editor.bridge.content = res.stdout
                logger.debug("Formatted with Ruff")
        except Exception:
            logger.exception("Error formatting code with ruff:")

    @Slot()
    def _on_new_clicked(self) -> None:
        self._active_filepath = None
        self.editor.bridge.open_tab(
            f"file:///workspace/untitled_{uuid4().hex[:4]}.py",
            get_default_script(),
            language="python",
            is_main=False,
        )

    @Slot()
    def _on_open_clicked(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open VapourSynth Script",
            "",
            "VapourSynth Script (*.vpy *.py);;All Files (*)",
        )
        if filepath:
            try:
                p = Path(filepath)
                content = p.read_text(encoding="utf-8")
                self._active_filepath = p
                self.editor.bridge.open_tab(p.as_uri(), content, language="python", is_main=False)
            except Exception:
                logger.exception("Error opening script:")

    @run_in_background(name="SaveScript")
    def _save_script(self, filepath: Path, content: str) -> None:
        self.statusSavingScriptStarted.emit("Saving script...")
        try:
            filepath.write_text(content, encoding="utf-8")
            self.statusSavingScriptFinished.emit("Saved")
            self.loop.from_thread(self.editor.bridge.tab_saved, filepath.as_uri(), self._active_tab_uri or "")
        except Exception:
            logger.exception("Error saving script:")

    @Slot(int)
    def _on_console_resized(self, width: int) -> None:
        GlobalConsoleHub.set_width(self.editor.bridge, width)

    @Slot()
    def _on_settings_changed(self) -> None:
        self._send_pyright_settings()
        self._send_editor_options()
        self.editor.bridge.set_theme(self.settings.global_.options.theme)

    @run_in_background(name="GenerateStubs")
    def _generate_stubs(self, force: bool = False) -> None:
        stubs_dir = get_stubs_dir()
        stub_file = stubs_dir / "vapoursynth" / "__init__.pyi"

        if force or not stub_file.exists():
            stubs_dir.mkdir(parents=True, exist_ok=True)
            try:
                logger.debug("Generating VapourSynth stubs at %s...", stub_file)
                with self._env_stubs.use():
                    from vsstubs import output_stubs

                    output_stubs(input_file=None, output=stub_file)
                logger.debug("VapourSynth stubs generated successfully.")
            except Exception:
                logger.exception("Failed to generate VapourSynth stubs:")

        self._send_pyright_settings()

    def _send_pyright_settings(self) -> None:
        settings = self.settings.global_.basedpyright
        if (stubs_path := get_stubs_dir()) not in settings.extra_paths:
            settings.extra_paths.append(stubs_path)

        self.editor.bridge.update_lsp_settings("basedpyright", settings.model_dump_json(by_alias=True))

    def _send_editor_options(self) -> None:
        self.editor.bridge.update_editor_options(self.settings.global_.options.model_dump_json(by_alias=True))


class EditorShortcut(StrEnum):
    definition: ActionDefinition

    NEW_SCRIPT = "new_script", "New Script", "Ctrl+N"
    OPEN_SCRIPT = "open_script", "Open Script", "Ctrl+O"
    SAVE_SCRIPT = "save_script", "Save Script", "Ctrl+S"
    SAVE_SCRIPT_AS = "save_script_as", "Save Script As...", "Ctrl+Shift+S"
    FORMAT_DOCUMENT = "format_document", "Format Document", "Shift+Alt+F"
    TOGGLE_WORD_WRAP = "toggle_word_wrap", "Toggle Word Wrap", "Alt+Z"
    TOGGLE_CONSOLE = "toggle_console", "Toggle Console", "Ctrl+`"
    RUN_SCRIPT = "run_script", "Run Script", "F5"
    GENERATE_STUBS = "generate_stubs", "Generate Stubs", ""

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(f"jet_vsview_editor.{value}", label, default_key)
        return obj


class StackDock(QDockWidget):
    """QDockWidget containing the workspace stack (video preview & controls)."""

    def __init__(self, parent: QWidget, stack: QWidget) -> None:
        super().__init__("Preview", parent)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        container = QFrame(self, frameShape=QFrame.Shape.StyledPanel, frameShadow=QFrame.Shadow.Sunken)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(stack)
        self.setWidget(container)


class EditorWorkspace(
    BaseGenericFileWorkspace[ContentPath],
    VSEngineWorkspace[ContentPath],
    PluginWorkspace[GlobalSettings, LocalSettings],
):
    """Workspace with a Monaco editor for writing and running VapourSynth scripts."""

    title = "Editor"
    icon = IconName.CODE

    content_type = "code"

    identifier = "jet_vsview_editor"
    display_name = "Editor"

    shortcuts = (
        EditorShortcut.NEW_SCRIPT.definition,
        EditorShortcut.OPEN_SCRIPT.definition,
        EditorShortcut.SAVE_SCRIPT.definition,
        EditorShortcut.SAVE_SCRIPT_AS.definition,
        EditorShortcut.FORMAT_DOCUMENT.definition,
        EditorShortcut.TOGGLE_WORD_WRAP.definition,
        EditorShortcut.TOGGLE_CONSOLE.definition,
        EditorShortcut.RUN_SCRIPT.definition,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = ContentPath(
            get_default_script(),
            (SPath.cwd() / f"<vsview editor {uuid4().hex[:8].upper()}>").to_str(),
        )
        self.loaded_once = False

        self.setDockNestingEnabled(True)

        self.tbar.setVisible(False)
        self.content_area.setVisible(False)

        self.code_dock = MonacoEditorDock(self, self.loop, self.api, self.settings)
        self.code_dock.runClicked.connect(self._on_run_clicked)
        self.code_dock.mainFileChanged.connect(self._on_main_file_changed)
        self.code_dock.statusSavingScriptStarted.connect(self.statusLoadingStarted.emit)
        self.code_dock.statusSavingScriptFinished.connect(self.statusLoadingFinished.emit)

        self.stack_dock = StackDock(self, self.stack)

        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.code_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.stack_dock)

        self._central_widget.hide()

        self.stack.setCurrentWidget(self.loaded_page)

        self.api.globalSettingsChanged.connect(self.code_dock._on_settings_changed)
    @property
    @override
    def current_file_path(self) -> ContentPath | None:
        return ContentPath(p.read_text(), str(p)) if (p := self.code_dock.main_filepath) and p.is_file() else None


    @override
    def deleteLater(self) -> None:
        GlobalConsoleHub.unregister(self.code_dock.editor.bridge)
        self.code_dock.editor.bridge.disconnect_lsp()
        self.code_dock.lsp_manager.stop()
        super().deleteLater()

    @property
    @override
    def _script_content(self) -> Any:
        return self.content.code

    @property
    @override
    def _script_kwargs(self) -> dict[str, Any]:
        return {"filename": self.content.filename}

    @override
    def on_connected(self) -> None:
        GlobalConsoleHub.register(self.code_dock.editor.bridge)

    @override
    def on_disconnected(self) -> None:
        GlobalConsoleHub.unregister(self.code_dock.editor.bridge)

    @override
    def get_output_metadata(self) -> dict[int, str]:
        return output_metadata.get(self.content.filename, {})

    @override
    def loader(self) -> None:
        # Register source with linecache so traceback can display source lines for virtual files
        linecache.cache[self.content.filename] = (
            len(self.content.code),
            None,
            self.content.splitlines(keepends=True),
            self.content.filename,
        )

        return super().loader()

    @override
    def reload_content(self, code: str | None = None) -> UnifiedFuture[int]:
        self.content = ContentPath(fallback(code, self.code_dock.editor.bridge.content), self.content.filename)

        if not self.loaded_once:
            self.loaded_once = True
            return self.load_content(
                self.content,
                self.playback.state.current_frame,
                self.playback.state.current_time.total_seconds(),
                self.outputs_manager.current_video_index,
            )

        return super().reload_content()

    @run_in_loop(return_future=False)
    @override
    def set_loaded_page(self) -> None:
        self.content_area.setVisible(True)
        self.tbar.setVisible(True)
        self.stack.setCurrentWidget(self.loaded_page)

    @run_in_loop(return_future=False)
    @override
    def set_error_page(self) -> None:
        self.content_area.setVisible(False)
        self.tbar.setVisible(False)
        self.stack.setCurrentWidget(self.loaded_page)
        self.disable_reloading = False
        self.loaded_once = False  # Reset so next run does fresh load_content

    @Slot(object)
    def _on_main_file_changed(self, new_path: Path | None) -> None:
        if new_path:
            self.content = ContentPath(new_path.read_text(), str(new_path))

            if new_path.is_file():
                self.init_load()
                self.api.localSettingsChanged.emit(str(SettingsManager.local_settings_path(new_path)))

    def _on_run_clicked(self) -> None:
        with GlobalConsoleHub.bind_execution(self.code_dock.editor.bridge):
            self.reload_content(code=self.code_dock.editor.bridge.main_content)
