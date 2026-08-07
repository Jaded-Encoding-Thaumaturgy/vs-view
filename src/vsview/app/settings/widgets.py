from __future__ import annotations

from collections.abc import Sequence
from logging import getLogger
from typing import override

from jetpytools import to_arr
from pydantic import TypeAdapter, ValidationError
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QColorDialog,
    QCompleter,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...assets import IconName, get_monospace_font
from ..icon import IconReloadMixin

logger = getLogger(__name__)


class LoginCredentialsInput(QWidget):
    """Widget for entering login credentials."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setLayout(layout := QVBoxLayout(self))
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.username_edit = QLineEdit(self, placeholderText="Username")
        layout.addWidget(self.username_edit)

        self.password_edit = QLineEdit(self, placeholderText="Password")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        layout.addWidget(self.password_edit)

    @property
    def credentials(self) -> tuple[str, str]:
        return self.username_edit.text().strip(), self.password_edit.text()

    @credentials.setter
    def credentials(self, value: tuple[str, str]) -> None:
        self.username_edit.setText(value[0])
        self.password_edit.setText(value[1])


class ColorPickerInput(QWidget):
    """Widget for selecting a color with a preview swatch and a hex entry."""

    class Swatch(QLabel):
        """Swatch widget for displaying a color."""

        clicked = Signal()

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)

            self.setCursor(Qt.CursorShape.PointingHandCursor)

        @override
        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setLayout(layout := QHBoxLayout(self))
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.swatch = self.Swatch(self)
        self.swatch.setFixedSize(20, 20)
        self.swatch.clicked.connect(self._pick_color)
        layout.addWidget(self.swatch)

        self.hex_edit = QLineEdit(self, maxLength=9, placeholderText="#RRGGBBAA", alignment=Qt.AlignmentFlag.AlignRight)
        self.hex_edit.setFont(get_monospace_font())
        self.hex_edit.setFixedWidth(self.hex_edit.fontMetrics().horizontalAdvance("#FFFFFFFF") + 5)
        self.hex_edit.editingFinished.connect(self._on_hex_edited)

        layout.addWidget(self.hex_edit)

        layout.addStretch()

        self._color = QColor(Qt.GlobalColor.white)
        self._update_ui()

    @property
    def color(self) -> QColor:
        return self._color

    @color.setter
    def color(self, value: QColor) -> None:
        if value.isValid():
            self._color = value
            self._update_ui()

    def _update_ui(self) -> None:
        self.swatch.setStyleSheet(
            f"background-color: {self._color.name()}; border: 1px solid gray; border-radius: 3px;"
        )
        if not self.hex_edit.hasFocus():
            self.hex_edit.setText(self._color.name().upper())

    @Slot()
    def _on_hex_edited(self) -> None:
        text = self.hex_edit.text()
        if QColor.isValidColorName(text):
            self._color = QColor(text)
            self._update_ui()
        else:
            self._update_ui()  # Revert to current color if invalid

    @Slot()
    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            self._color,
            self,
            "Select Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self.color = color


class ListEditWidget[T](QWidget, IconReloadMixin):
    """Structured list editor with Add/Remove buttons."""

    def __init__(
        self,
        value_type: type[T],
        parent: QWidget | None = None,
        default_value: T | Sequence[T] | None = None,
        dialog_label_text: str | None = None,
        completions: Sequence[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.value_type = value_type
        self.adapter = TypeAdapter[T](value_type)
        self.default_value = default_value

        self.dialog = QInputDialog(self)
        self.dialog.setInputMode(QInputDialog.InputMode.TextInput)
        self.dialog.setWindowTitle("Add Item")
        self.dialog.setLabelText(dialog_label_text or f"Enter {self.value_type.__name__}:")
        self.dialog.finished.connect(self._on_dialog_finished)

        self.dialog_line_edit = self.dialog.findChild(QLineEdit)

        if completions and self.dialog_line_edit:
            self.completer: QCompleter | None = QCompleter(
                completions,
                self.dialog_line_edit,
                caseSensitivity=Qt.CaseSensitivity.CaseInsensitive,
            )
            self.dialog_line_edit.setCompleter(self.completer)
        else:
            self.completer = None

        self.setLayout(layout := QVBoxLayout(self))
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.list_widget.setMaximumHeight(100)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        self.add_btn = self.make_tool_button(IconName.PLUS, "Add Item", self)
        self.add_btn.clicked.connect(self.dialog.open)

        self.remove_btn = self.make_tool_button(IconName.MINUS, "Remove Item", self)
        self.remove_btn.clicked.connect(self._remove_selected)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    @Slot()
    def _on_dialog_finished(self, result: int) -> None:
        if not result:
            return

        if text := self.dialog.textValue():
            self.validate_text(text)

        self.dialog.setTextValue("")

    @Slot()
    def _remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def get_values(self) -> list[T]:
        values = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text()
            try:
                values.append(self.adapter.validate_python(text))
            except ValidationError as e:
                logger.warning("Invalid value: %s", e)

        if not values and self.default_value:
            return to_arr(self.default_value)

        return values

    def set_values(self, values: list[T]) -> None:
        self.list_widget.clear()

        if not values and self.default_value:
            values = to_arr(self.default_value)

        for v in values:
            self.list_widget.addItem(str(v))

    def validate_text(self, text: str) -> None:
        try:
            self.adapter.validate_python(text)
            self.list_widget.addItem(text)
        except ValidationError as e:
            logger.error("Invalid value: %s", e)


class PathListEditWidget(QWidget, IconReloadMixin):
    """Widget for managing multiple directory paths with Add/Remove buttons."""

    def __init__(self, parent: QWidget | None = None, default_value: Sequence[str] | None = None) -> None:
        super().__init__(parent)
        self.default_value = default_value

        self.setLayout(layout := QVBoxLayout(self))
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.list_widget.setMaximumHeight(100)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        self.add_btn = self.make_tool_button(IconName.PLUS, "Add Directory", self)
        self.add_btn.clicked.connect(self._add_directory)

        self.remove_btn = self.make_tool_button(IconName.MINUS, "Remove Selected", self)
        self.remove_btn.clicked.connect(self._remove_selected)

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    @Slot()
    def _add_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Directory")
        if path:
            self.list_widget.addItem(path)

    @Slot()
    def _remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def get_values(self) -> list[str]:
        values = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
        return to_arr(self.default_value) if not values and self.default_value else values

    def set_values(self, values: Sequence[str]) -> None:
        self.list_widget.clear()
        if not values and self.default_value:
            values = self.default_value
        self.list_widget.addItems(values)


class FilePickerWidget(QWidget, IconReloadMixin):
    """Widget for selecting a single file path with a Browse button."""

    def __init__(
        self,
        parent: QWidget | None = None,
        file_filter: str = "All Files (*.*)",
        dialog_title: str = "Select File",
    ) -> None:
        super().__init__(parent)
        self.file_filter = file_filter
        self.dialog_title = dialog_title

        self.setLayout(layout := QHBoxLayout(self))
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.line_edit = QLineEdit(self)
        self.line_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.line_edit)

        self.browse_btn = self.make_tool_button(IconName.FILE_IMPORT, "Browse File", self)
        self.browse_btn.clicked.connect(self._browse_file)
        self.clear_btn = self.make_tool_button(IconName.X_CIRCLE, "Clear selection", self)
        self.clear_btn.clicked.connect(self._clear_selection)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.clear_btn)

    @Slot()
    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.dialog_title, self.line_edit.text(), self.file_filter)
        if path:
            self.line_edit.setText(path)

    @Slot()
    def _clear_selection(self) -> None:
        self.line_edit.setText(None)

    @property
    def file_path(self) -> str:
        return self.line_edit.text().strip()

    @file_path.setter
    def file_path(self, value: str) -> None:
        self.line_edit.setText(value)


class CustomSpinBox(QSpinBox):
    """A spinbox that supports rendering special strings for min and/or max values."""

    def __init__(
        self,
        parent: QWidget | None = None,
        min_text: str | None = None,
        max_text: str | None = None,
        custom_suffix: str = "",
    ) -> None:
        super().__init__(parent)
        self.min_text = min_text
        self.max_text = max_text
        self.custom_suffix = custom_suffix

    @override
    def textFromValue(self, val: int) -> str:
        if val == self.minimum() and self.min_text is not None:
            return self.min_text
        if val == self.maximum() and self.max_text is not None:
            return self.max_text
        return f"{val}{self.custom_suffix}"

    @override
    def valueFromText(self, text: str) -> int:
        if self.min_text and text.strip().lower() == self.min_text.strip().lower():
            return self.minimum()
        if self.max_text and text.strip().lower() == self.max_text.strip().lower():
            return self.maximum()

        clean_text = text
        if self.custom_suffix:
            trimmed = self.custom_suffix.strip()
            if text.endswith(self.custom_suffix):
                clean_text = text[: -len(self.custom_suffix)]
            elif trimmed and text.endswith(trimmed):
                clean_text = text[: -len(trimmed)]
        try:
            return int(clean_text.strip())
        except ValueError:
            return super().valueFromText(text)
