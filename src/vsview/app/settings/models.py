"""Settings models for vsview."""

from __future__ import annotations

import os
import sys
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import KW_ONLY, dataclass, field
from datetime import time, timedelta
from enum import StrEnum
from functools import wraps
from logging import getLogger
from operator import attrgetter
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Concatenate,
    Literal,
    NamedTuple,
    Self,
    get_args,
    get_origin,
    get_type_hints,
)

from jetpytools import SPath, SupportsRichComparison, classproperty, to_arr
from platformdirs import user_config_path
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    ValidationError,
    model_serializer,
)
from pygments.styles import get_all_styles as get_pygments_styles
from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtGui import QColor, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStyleFactory,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ...assets import ICON_PROVIDERS, IconName, IconReloadMixin, get_monospace_font
from ...env import getenv_bool
from .enums import Resizer
from .secrets import SecretsManager

logger = getLogger(__name__)


class WidgetMetadataMeta(ABCMeta):
    """Metaclass for WidgetMetadata."""

    def __new__[MetaSelf: WidgetMetadataMeta](
        mcls: type[MetaSelf],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        *,
        tooltip: bool = True,
        **kwargs: Any,
    ) -> MetaSelf:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)

        if tooltip:
            func = getattr(cls, "create_widget")

            if not getattr(func, "__isdecorated__", False):
                setattr(cls, "create_widget", mcls._set_tool_tip(func))

        return cls

    @staticmethod
    def _set_tool_tip[WidgetMetadataT: WidgetMetadata[QWidget], **P](
        method: Callable[Concatenate[WidgetMetadataT, P], QWidget],
    ) -> Callable[Concatenate[WidgetMetadataT, P], QWidget]:
        """Decorator to set the tooltip of the widget."""

        @wraps(method)
        def wrapper(self: WidgetMetadataT, *args: P.args, **kwargs: P.kwargs) -> QWidget:
            widget = method(self, *args, **kwargs)

            if self.tooltip:
                widget.setToolTip(self.tooltip)

            return widget

        setattr(wrapper, "__isdecorated__", True)

        return wrapper


@dataclass(frozen=True, slots=True)
class WidgetMetadata[W: QWidget](ABC, metaclass=WidgetMetadataMeta):
    """Base class for widget metadata."""

    label: str
    """Display label for the setting."""

    _: KW_ONLY

    tooltip: str | None = None
    """Tooltip text for the setting."""
    to_ui: Callable[[Any], Any] | None = None
    """Transform value before loading into UI (e.g., seconds -> ms)."""
    from_ui: Callable[[Any], Any] | None = None
    """Transform value after extracting from UI (e.g., ms -> seconds)."""

    @abstractmethod
    def create_widget(self, parent: QWidget | None = None) -> W:
        """Create and configure a widget for this metadata."""

    @abstractmethod
    def load_value(self, widget: W, value: Any) -> None:
        """Load a value into the widget."""

    @abstractmethod
    def get_value(self, widget: W) -> Any:
        """Get the current value from the widget."""

    @contextmanager
    def apply_transform(self, value: Any, transform: Callable[[Any], Any] | None) -> Iterator[Any]:
        if transform:
            try:
                yield transform(value)
            except Exception as e:
                logger.error("Failed to convert value: %r with error: %s", value, e)
                raise
        else:
            yield value


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


@dataclass(frozen=True)
class Login(WidgetMetadata[LoginCredentialsInput]):
    """Login credentials widget metadata."""

    namespace: str
    """Namespace for the secret."""
    context: str
    """Context for the secret"""

    _: KW_ONLY
    to_ui: None = None
    from_ui: None = None

    def create_widget(self, parent: QWidget | None = None) -> LoginCredentialsInput:
        return LoginCredentialsInput(parent)

    def load_value(self, widget: LoginCredentialsInput, value: str) -> None:
        username = value
        password = self._get_password(username) if username else ""

        if username and password:
            widget.credentials = username, password

    def get_value(self, widget: LoginCredentialsInput) -> Any:
        username, password = widget.credentials
        self._set_password((username, password))
        return username

    def _get_password(self, username: str) -> str:
        # Store the username for removal if the user passes a new username
        object.__setattr__(self, "_old_username", username)
        return SecretsManager.get(self.namespace, self.context, username) or ""

    def _set_password(self, cred: tuple[str, str]) -> None:
        username, password = cred

        # Remove old entry if the username has been changed
        with suppress(AttributeError):
            SecretsManager.delete(self.namespace, self.context, object.__getattribute__(self, "_old_username"))
            object.__delattr__(self, "_old_username")

        if username:
            SecretsManager.set(self.namespace, self.context, username, password)


class ColorPickerInput(QWidget):
    """Widget for selecting a color with a preview swatch and a hex entry."""

    class Swatch(QLabel):
        """Swatch widget for displaying a color."""

        clicked = Signal()

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)

            self.setCursor(Qt.CursorShape.PointingHandCursor)

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

    def _on_hex_edited(self) -> None:
        text = self.hex_edit.text()
        if QColor.isValidColorName(text):
            self._color = QColor(text)
            self._update_ui()
        else:
            self._update_ui()  # Revert to current color if invalid

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            self._color,
            self,
            "Select Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self.color = color


@dataclass(frozen=True, slots=True)
class ColorPicker(WidgetMetadata[ColorPickerInput]):
    """ColorPicker widget metadata."""

    def create_widget(self, parent: QWidget | None = None) -> ColorPickerInput:
        return ColorPickerInput(parent)

    def load_value(self, widget: ColorPickerInput, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            if isinstance(value, str):
                widget.color = QColor(value)
            elif isinstance(value, QColor):
                widget.color = value

    def get_value(self, widget: ColorPickerInput) -> Any:
        with self.apply_transform(widget.color, self.from_ui) as value:
            if isinstance(value, QColor):
                return value.name()
            return value


@dataclass(frozen=True, slots=True)
class Checkbox(WidgetMetadata[QCheckBox]):
    """Checkbox widget metadata."""

    text: str
    """Text displayed next to the checkbox."""

    tristate: bool = field(default=False, kw_only=True)
    """Whether the checkbox is a tri-state checkbox"""

    def create_widget(self, parent: QWidget | None = None) -> QCheckBox:
        return QCheckBox(self.text, parent, tristate=self.tristate)

    def load_value(self, widget: QCheckBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            if self.tristate:
                if isinstance(value, bool):
                    widget.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                else:
                    widget.setCheckState(Qt.CheckState(value))
            else:
                widget.setChecked(bool(value))

    def get_value(self, widget: QCheckBox) -> Any:
        val = widget.checkState() if self.tristate else widget.isChecked()
        with self.apply_transform(val, self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class LineEdit(WidgetMetadata[QLineEdit]):
    """LineEdit widget metadata."""

    def create_widget(self, parent: QWidget | None = None) -> QLineEdit:
        return QLineEdit(parent)

    def load_value(self, widget: QLineEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.setText(value)

    def get_value(self, widget: QLineEdit) -> Any:
        with self.apply_transform(widget.text(), self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class Dropdown(WidgetMetadata[QComboBox]):
    """Dropdown/ComboBox widget metadata."""

    items: Iterable[tuple[str, Any]]
    """Iterable of (display_text, value) tuples."""

    def create_widget(self, parent: QWidget | None = None) -> QComboBox:
        widget = QComboBox(parent)
        for display_text, value in self.items:
            widget.addItem(display_text, value)
        return widget

    def load_value(self, widget: QComboBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            index = widget.findData(value)
            if index >= 0:
                widget.setCurrentIndex(index)

    def get_value(self, widget: QComboBox) -> Any:
        with self.apply_transform(widget.currentData(), self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class Spin(WidgetMetadata[QSpinBox]):
    """SpinBox widget metadata for integers."""

    min: int = 0
    max: int = 100
    suffix: str = ""

    def create_widget(self, parent: QWidget | None = None) -> QSpinBox:
        widget = QSpinBox(parent)
        widget.setMinimum(self.min)
        widget.setMaximum(self.max)
        widget.setSuffix(self.suffix)
        return widget

    def load_value(self, widget: QSpinBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.setValue(value)

    def get_value(self, widget: QSpinBox) -> Any:
        with self.apply_transform(widget.value(), self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class DoubleSpin(WidgetMetadata[QDoubleSpinBox]):
    """DoubleSpinBox widget metadata for floats."""

    min: float = 0.0
    max: float = 100.0
    suffix: str = ""
    decimals: int = 2

    def create_widget(self, parent: QWidget | None = None) -> QDoubleSpinBox:
        widget = QDoubleSpinBox(parent)
        widget.setMinimum(self.min)
        widget.setMaximum(self.max)
        widget.setSuffix(self.suffix)
        widget.setDecimals(self.decimals)
        widget.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        return widget

    def load_value(self, widget: QDoubleSpinBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.setValue(value)

    def get_value(self, widget: QDoubleSpinBox) -> Any:
        with self.apply_transform(widget.value(), self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class PlainTextEdit[T: SupportsRichComparison](WidgetMetadata[QPlainTextEdit]):
    """PlainTextEdit widget for editing a list of values (one per line)."""

    value_type: type[T]
    """Type of values in the list."""
    max_height: int = 120
    """Maximum height of the widget in pixels."""
    default_value: T | None = field(default=None, kw_only=True)
    """Default value for the setting."""

    def __post_init__(self) -> None:
        if self.from_ui is None:
            object.__setattr__(self, "from_ui", self._list_from_ui)
        if self.to_ui is None:
            object.__setattr__(self, "to_ui", self._list_to_ui)

    def create_widget(self, parent: QWidget | None = None) -> QPlainTextEdit:
        widget = QPlainTextEdit(parent)
        widget.setMinimumHeight(self.max_height)
        widget.setMaximumHeight(self.max_height)
        return widget

    def load_value(self, widget: QPlainTextEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.setPlainText(str(value))

    def get_value(self, widget: QPlainTextEdit) -> Any:
        with self.apply_transform(widget.toPlainText(), self.from_ui) as value:
            return value

    def _list_to_ui(self, values: list[T]) -> str:
        return "\n".join(str(v) for v in values)

    def _list_from_ui(self, text: str) -> list[T]:
        adapter = TypeAdapter[T](self.value_type)
        values = list[T]()

        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    values.append(adapter.validate_python(line))
                except ValidationError as e:
                    logger.error("Failed to parse value: %r with error: %s", line, e)

        if values:
            return sorted(set(values))

        if self.default_value is not None:
            return [self.default_value]

        raise ValueError("Default value is required for PlainTextEdit widget")


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

    def _on_dialog_finished(self, result: int) -> None:
        if not result:
            return

        if text := self.dialog.textValue():
            self.validate_text(text)

        self.dialog.setTextValue("")

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


@dataclass(frozen=True, slots=True)
class ListEdit[T: SupportsRichComparison](WidgetMetadata[ListEditWidget[T]]):
    """ListEdit widget metadata using a QListWidget."""

    value_type: type[T]
    """Type of values in the list."""

    default_value: T | Sequence[T] | None = field(default=None, kw_only=True)
    """Default value for the setting."""

    _: KW_ONLY

    dialog_label_text: str | None = None
    """Label text for the dialog."""

    completions: Sequence[str] | None = None
    """Completions for the dialog."""

    def create_widget(self, parent: QWidget | None = None) -> ListEditWidget[T]:
        return ListEditWidget(self.value_type, parent, self.default_value, self.dialog_label_text, self.completions)

    def load_value(self, widget: ListEditWidget[T], value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.set_values(value)

    def get_value(self, widget: ListEditWidget[T]) -> Any:
        with self.apply_transform(widget.get_values(), self.from_ui) as value:
            return sorted(set(value)) if isinstance(value, list) else value


@dataclass(frozen=True, slots=True)
class WidgetTimeEdit(WidgetMetadata[QTimeEdit]):
    """TimeEdit widget for times"""

    min: QTime | None = None
    max: QTime | None = None
    display_format: str | None = None

    if TYPE_CHECKING:
        _: KW_ONLY

        to_ui: Callable[[Any], QTime] | None = None
        from_ui: Callable[[QTime], Any] | None = None

    def create_widget(self, parent: QWidget | None = None) -> QTimeEdit:
        widget = QTimeEdit(parent)

        if self.min:
            widget.setMinimumTime(self.min)
        if self.max:
            widget.setMaximumTime(self.max)

        if self.display_format:
            widget.setDisplayFormat(self.display_format)

        return widget

    def load_value(self, widget: QTimeEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.setTime(QTime(value))

    def get_value(self, widget: QTimeEdit) -> Any:
        with self.apply_transform(widget.time(), self.from_ui) as value:
            return value


class SettingEntry(NamedTuple):
    """A setting field with its key path, section, and metadata."""

    key: str
    """Dotted path to the setting (e.g., 'timeline.mode')."""
    section: str
    """UI section name (e.g., 'Timeline')."""
    metadata: WidgetMetadata[QWidget]
    """Widget metadata for this setting."""


def _get_widget_metadata(annotation: Any) -> WidgetMetadata[QWidget] | None:
    if get_origin(annotation) is Annotated:
        for arg in get_args(annotation)[1:]:
            if isinstance(arg, WidgetMetadata):
                return arg
    return None


def extract_settings(model: type[BaseModel], prefix: str = "", section: str | None = None) -> list[SettingEntry]:
    """Extract SettingEntry list from a Pydantic model's Annotated fields."""
    result = list[SettingEntry]()
    hints = get_type_hints(model, include_extras=True)

    model_section = getattr(model, "__section__", None) or section

    for field_name, annotation in hints.items():
        key = f"{prefix}{field_name}" if prefix else field_name
        metadata = _get_widget_metadata(annotation)

        if metadata is None:
            # Check if it's a nested BaseModel
            inner_type = get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation
            if isinstance(inner_type, type) and issubclass(inner_type, BaseModel):
                result.extend(extract_settings(inner_type, prefix=f"{key}.", section=model_section))
        else:
            if model_section is None:
                raise ValueError(f"No section defined for setting '{key}'. Add __section__ to the model class.")
            result.append(SettingEntry(key=key, section=model_section, metadata=metadata))

    return result


# -----------------------------------------------------------------------------------
class ActionDefinition(str):
    """
    Unified definition and identifier for a shortcut action.
    """

    label: str
    """Human-readable display label"""

    default_key: str
    """Default key sequence (can be empty)"""

    def __new__(cls, id: str, label: str, default_key: str = "") -> Self:
        self = super().__new__(cls, id)
        self.label = label
        self.default_key = default_key
        return self

    def __repr__(self) -> str:
        return f"ActionDefinition({super().__repr__()}, label={self.label!r}, default_key={self.default_key!r})"


class ActionID(StrEnum):
    """Identifiers for keyboard shortcut actions."""

    definition: ActionDefinition

    # Menu actions
    LOAD_SCRIPT = "menu.new.load_script", "Load Script", "Ctrl+O"
    LOAD_FILE = "menu.new.load_file", "Load File", "Ctrl+Shift+O"
    WORKSPACE_SCRIPT = "menu.new.workspace.new_script", "New Script Workspace", ""
    WORKSPACE_FILE = "menu.new.workspace.new_file", "New File Workspace", ""
    WORKSPACE_QUICK_SCRIPT = "menu.new.workspace.new_quick_script", "New Quick Script", ""

    # Workspace
    RELOAD = "workspace.loader.reload", "Reload Script", "Ctrl+R"
    RUN_QUICK_SCRIPT = "workspace.quickscript.run", "Run Quick Script", "F5"

    # Tab manager
    SYNC_PLAYHEAD = "workspace.loader.tab.sync_playhead", "Sync Playhead", ""
    SYNC_ZOOM = "workspace.loader.tab.sync_zoom", "Sync Zoom", ""
    SYNC_SCROLL = "workspace.loader.tab.sync_scroll", "Sync Scroll", ""
    AUTOFIT_ALL_VIEWS = "workspace.loader.tab.autofit_all_views", "Global Autofit", "Ctrl+A"

    # View actions
    RESET_ZOOM = "workspace.loader.view.reset_zoom", "Reset Zoom", "Esc"
    AUTOFIT = "workspace.loader.view.autofit", "Autofit View", "Ctrl+Shift+A"
    TOGGLE_SAR = "workspace.loader.view.sar", "Toggle SAR view", ""
    SAVE_CURRENT_IMAGE = "workspace.loader.view.save_current_image", "Save Current Image", "Ctrl+Shift+S"
    COPY_IMAGE_TO_CLIPBOARD = "workspace.loader.view.copy_image_to_clipboard", "Copy Image to Clipboard", "Ctrl+S"
    TOGGLE_PLUGIN_PANEL = "workspace.loader.view.toggle_plugin_panel", "Toggle Plugin Panel", "Ctrl+P"

    # Timeline actions
    COPY_CURRENT_FRAME = "workspace.loader.timeline.copy_current_frame", "Copy Current Frame", "S"
    COPY_CURRENT_TIME = "workspace.loader.timeline.copy_current_time", "Copy Current Time", "Shift+S"
    PLAY_PAUSE = "workspace.loader.timeline.play_pause", "Play / Pause", "Space"
    SEEK_PREVIOUS_FRAME = "workspace.loader.timeline.seek_previous_frame", "Seek Previous Frame", "Left"
    SEEK_NEXT_FRAME = "workspace.loader.timeline.seek_next_frame", "Seek Next Frame", "Right"
    SEEK_N_FRAMES_BACK = "workspace.loader.timeline.seek_n_frames_back", "Seek N Frames Back", "Shift+Left"
    SEEK_N_FRAMES_FORWARD = "workspace.loader.timeline.seek_n_frames_forward", "Seek N Frames Forward", "Shift+Right"
    SEEK_FIRST_FRAME = "workspace.loader.timeline.seek_first_frame", "Seek First Frame", ""
    SEEK_LAST_FRAME = "workspace.loader.timeline.seek_last_frame", "Seek Last Frame", ""

    # Tab switching
    SWITCH_TAB_0 = "workspace.loader.tab.switch_0", "Switch to Output 0", "1"
    SWITCH_TAB_1 = "workspace.loader.tab.switch_1", "Switch to Output 1", "2"
    SWITCH_TAB_2 = "workspace.loader.tab.switch_2", "Switch to Output 2", "3"
    SWITCH_TAB_3 = "workspace.loader.tab.switch_3", "Switch to Output 3", "4"
    SWITCH_TAB_4 = "workspace.loader.tab.switch_4", "Switch to Output 4", "5"
    SWITCH_TAB_5 = "workspace.loader.tab.switch_5", "Switch to Output 5", "6"
    SWITCH_TAB_6 = "workspace.loader.tab.switch_6", "Switch to Output 6", "7"
    SWITCH_TAB_7 = "workspace.loader.tab.switch_7", "Switch to Output 7", "8"
    SWITCH_TAB_8 = "workspace.loader.tab.switch_8", "Switch to Output 8", "9"
    SWITCH_TAB_9 = "workspace.loader.tab.switch_9", "Switch to Output 9", "0"
    SWITCH_PREVIOUS_TAB = "workspace.loader.tab.switch_previous_tab", "Switch to Previous Output", ""
    SWITCH_NEXT_TAB = "workspace.loader.tab.switch_next_tab", "Switch to Next Output", ""

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(value, label, default_key)
        return obj


class ShortcutConfig(BaseModel):
    """Configuration for a single keyboard shortcut."""

    action_id: str
    key_sequence: Annotated[str, AfterValidator(lambda v: v if not QKeySequence(v).isEmpty() else "")]


class AppearanceSettings(BaseModel):
    """Settings for the application appearance."""

    __section__ = "Appearance"

    theme: Annotated[
        Qt.ColorScheme | None,
        Dropdown(
            label="Theme",
            items=[
                ("System Default", None),
                ("Light", Qt.ColorScheme.Light),
                ("Dark", Qt.ColorScheme.Dark),
            ],
            tooltip=(
                "Application theme.\n"  #
                "You may have to restart the application for the changes to fully take effect."
            ),
        ),
    ] = None

    style: Annotated[
        str | None,
        Dropdown(
            label="Style",
            items=[(k.title(), k) for k in QStyleFactory.keys()],  # noqa: SIM118
            tooltip=(
                "Application style.\n"  #
                "You may have to restart the application for the changes to fully take effect."
            ),
        ),
    ] = None

    icon_provider: Annotated[
        str,
        Dropdown(
            label="Icon Provider",
            items=[(provider.name, provider_id) for provider_id, provider in ICON_PROVIDERS.items()],
            tooltip="Provider for icon rendering",
        ),
    ] = "phosphor"

    icon_weight: Annotated[
        str,
        Dropdown(
            label="Icon Weight",
            items=[],  # Populated dynamically based on selected provider
            tooltip="Weight of icons",
        ),
    ] = "regular"

    editor_theme: Annotated[
        str,
        Dropdown(
            label="Editor Theme",
            items=[
                (style_name.replace("-", " ").replace("_", " ").title(), style_name)
                for style_name in sorted(get_pygments_styles())
            ],
            tooltip="Theme for the editor of the Quick Script workspace",
        ),
    ] = "gruvbox-dark"

    tab_bar: Annotated[
        Qt.CheckState,
        PlainValidator(lambda v: v if isinstance(v, Qt.CheckState) else Qt.CheckState(v)),
        PlainSerializer(lambda v: v.value, return_type=int),
        Checkbox(
            label="Tab Bar",
            text="Control the visibility of the tab bar",
            tooltip=(
                "Control the visibility of the tab bar.\n"
                "- Checked: Always visible\n"
                "- Unchecked: Always hidden\n"
                "- Partially Checked: Visible only when multiple tabs are open"
            ),
            tristate=True,
        ),
    ] = Qt.CheckState.PartiallyChecked

    sidebar_visible: bool = True


# Settings Models with Annotated Widget Metadata and Defaults
class TimelineSettings(BaseModel):
    """Settings for the timeline component."""

    __section__ = "Timeline"

    mode: Annotated[
        Literal["frame", "time"],
        Dropdown(
            label="Display Mode",
            items=[("Frame", "frame"), ("Time", "time")],
            tooltip="Display mode for the timeline",
        ),
    ] = "frame"

    display_scale: Annotated[
        float,
        DoubleSpin(
            label="Display Scale",
            min=1.0,
            max=2.5,
            suffix="x",
            decimals=2,
            tooltip="Display scale for the timeline",
        ),
    ] = 1.25

    notches_margin: Annotated[
        int,
        Spin(
            label="Label Notches Margin",
            min=1,
            max=100,
            suffix=" %",
            tooltip="Margin for notches in the timeline",
        ),
    ] = 10

    seek_step: Annotated[
        int,
        Spin(
            label="Default Seek Step",
            min=1,
            max=1_000_000,
            suffix=" frames",
            tooltip="Default seek step for the timeline",
        ),
    ] = 24

    view_hover_zoom: Annotated[
        bool,
        Checkbox(
            label="Hover Preview",
            text="Show zoomed preview on hover",
            tooltip="Show a zoomed preview of the timeline and notch labels when hovering.",
        ),
    ] = True

    hover_zoom_factor: Annotated[
        float,
        DoubleSpin(
            label="Hover Zoom Factor",
            min=1.0,
            max=20.0,
            suffix="x",
            decimals=1,
            tooltip="Magnification factor for the hover preview.",
        ),
    ] = 8.0

    hover_zoom_radius: Annotated[
        int,
        Spin(
            label="Hover Zoom Radius",
            min=10,
            max=500,
            suffix=" px",
            tooltip="Radius of the zoomed area in pixels.",
        ),
    ] = 100


class PlaybackSettings(BaseModel):
    """Settings for the video playback stuff"""

    __section__ = "Playback"

    buffer_size: Annotated[
        int,
        Spin(
            label="Buffer Size",
            min=1,
            max=120,
            suffix=" frames",
            tooltip="Number of frames to buffer during playback",
        ),
    ] = 15

    audio_buffer_size: Annotated[
        int,
        Spin(
            label="Audio Buffer Size",
            min=1,
            max=10,
            suffix=" frames",
            tooltip="Number of audio frames to buffer both in memory and on the audio device.\n"
            "3 is a good default. Increase it if you experience audio stuttering or dropouts.",
        ),
    ] = 3

    cache_size: Annotated[
        int,
        Spin(
            label="Cache size",
            min=0,
            max=1_000_000,
            suffix=" frames",
            tooltip="Number of frames to cache",
        ),
    ] = 10

    fps_history_size: Annotated[
        int,
        Spin(
            label="FPS History Size (0 = auto)",
            min=0,
            max=10_000,
            suffix=" frames",
            tooltip="Number of frames to keep in the FPS history",
        ),
    ] = 0

    default_volume: Annotated[
        float,
        Spin(
            label="Default Volume",
            suffix=" %",
            from_ui=lambda v: v / 100,
            to_ui=lambda v: int(v * 100),
        ),
    ] = 0.5

    downmix: Annotated[
        bool,
        Checkbox(
            label="Downmix",
            text="Always downmix surround to stereo",
            tooltip=(
                "Always downmix surround to stereo when AudioNode is passed through "
                "set_output and its downmix parameter is None (default)."
            ),
        ),
    ] = True

    audio_delay: Annotated[
        float,
        DoubleSpin(
            label="Audio Delay",
            min=-10000,
            max=10000,
            suffix=" ms",
            decimals=3,
            tooltip="Delay the audio in milliseconds. Positive values delay audio, negative values advance it.",
            to_ui=lambda v: v * 1000,
            from_ui=lambda v: v / 1000,
        ),
    ] = 0.0

    fps_update_interval: Annotated[
        float,
        DoubleSpin(
            label="FPS Update Interval",
            min=0.1,
            max=60.0,
            suffix=" s",
            decimals=1,
            tooltip="Interval for updating the FPS display in seconds",
        ),
    ] = 1.0


class ViewSettings(BaseModel):
    """Settings for the GraphicsView components"""

    __section__ = "View"

    png_compression_level: Annotated[
        int,
        Spin(
            label="PNG Compression Level",
            min=-1,
            max=100,
            suffix="",
            tooltip="The PNG Compression level.\n"
            "The default -1 uses zlib level ~4\n"
            "- Smallest file (zlib level 9): 0\n"
            "- Fastest save (zlib level 0): 100",
        ),
    ] = -1

    packing_method: Annotated[
        str,
        Dropdown(
            label="Packing Method",
            items=[
                ("Auto", "auto"),
                ("vszip", "vszip"),
                ("Cython", "cython"),
                ("NumPy", "numpy"),
                ("Python (slow)", "python"),
            ],
            tooltip="Packing method for the views",
        ),
    ] = "auto"

    bit_depth: Annotated[
        int,
        Dropdown(
            label="Bit Depth",
            items=[("8-bit", 8), ("10-bit", 10)],
            tooltip="Bit depth for the views",
        ),
    ] = 8

    dither_type: Annotated[
        str,
        Dropdown(
            label="Dithering Method",
            items=[
                ("None (Round to nearest)", "none"),
                ("Ordered (Bayer patterned dither)", "ordered"),
                ("Random (Pseudo-random noise of magnitude 0.5)", "random"),
                ("Error Diffusion (Floyd-Steinberg)", "error_diffusion"),
            ],
        ),
    ] = "random"

    chroma_resizer: Annotated[
        Resizer,
        Dropdown(
            label="Chroma Resizer",
            items=[(v, v) for v in Resizer],
            tooltip="Chroma resizer for the views",
        ),
    ] = Resizer.LANCZOS3

    props_policy: Annotated[
        Literal["error", "warn", "ignore"],
        Dropdown(
            label="Frame property policy",
            items=[(policy.title(), policy) for policy in ("error", "warn", "ignore")],
            tooltip=(
                "Handles missing or unspecified color properties (_Transfer, _Primaries and _Matrix).\n\n"
                "– Error: Conversion fails if properties are missing.\n"  # noqa: RUF001
                "– Warn: No conversion is performed for missing properties. A warning is logged instead.\n"  # noqa: RUF001
                "– Ignore: Same as Warn but no warnings are emitted to the log."  # noqa: RUF001
            ),
        ),
    ] = "error"

    zoom_factors: Annotated[
        list[float],
        LineEdit(
            label="Zoom Factors",
            tooltip="Zoom factors for the views.\nSeparate factors with commas.",
            to_ui=lambda v: ", ".join(map(str, v)),
            from_ui=lambda v: [float(x) for x in v.split(",") if x.strip()],
        ),
    ] = [0.25, 0.5, 0.75, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0]

    zoom_animation: Annotated[
        bool,
        Checkbox(
            label="Zoom animation",
            text="Enable zoom animation",
            tooltip="Enable zoom animation",
        ),
    ] = True

    checkerboard_size: Annotated[
        int,
        Spin(
            label="Checkerboard Size",
            min=2,
            max=256,
            suffix=" px",
            tooltip="The size of the checkerboard when displaying a clip with an alpha plane",
        ),
    ] = 16

    shade_opacity: Annotated[
        float,
        DoubleSpin(
            label="Shade Opacity",
            min=0.0,
            max=1.0,
            decimals=2,
            tooltip="Opacity of the darkened area outside the selection rect (0.0 to 1.0).",
        ),
    ] = 0.38

    selection_outline_color: Annotated[
        str,
        ColorPicker(
            label="Selection Outline Color",
            tooltip="Color of the selection rectangle outline",
        ),
    ] = "#FFD64F"

    copy_qimage: Annotated[
        bool,
        Checkbox(
            label="Copy QImage",
            text="Copy QImage to transfer ownership to Qt",
            tooltip=(
                "Copy the QImage memory to transfer ownership to Qt. "
                "Prevents crashes but uses more memory and is slower."
            ),
        ),
    ] = True


class WindowGeometry(BaseModel):
    """Window position and size."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    is_maximized: bool = False


class ViewTools(BaseModel):
    docks: dict[str, bool] = Field(default_factory=dict)
    panels: dict[str, bool] = Field(default_factory=dict)


class QtSettings(BaseModel):
    custom_colors: list[
        Annotated[
            QColor,
            PlainValidator(lambda v: v if isinstance(v, QColor) else QColor(v)),
            PlainSerializer(lambda color: color.name(), return_type=str),
        ]
    ] = Field(default_factory=list)
    model_config = ConfigDict(validate_assignment=True)

    def model_post_init(self, context: Any) -> None:
        if self.custom_colors:
            self.sync_to_dialog()
        else:
            self.sync_from_dialog()

    def sync_to_dialog(self) -> None:
        max_colors = QColorDialog.customCount()

        for i, color in enumerate(self.custom_colors):
            if i >= max_colors:
                break

            QColorDialog.setCustomColor(i, color)

    def sync_from_dialog(self) -> None:
        new_colors = list[QColor]()

        for i in range(QColorDialog.customCount()):
            if (color := QColorDialog.customColor(i)).isValid():
                new_colors.append(color)

        self.custom_colors.clear()
        self.custom_colors.extend(new_colors)

    @model_serializer(mode="wrap")
    def sync_before_serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        self.sync_from_dialog()
        return handler(self)


class BaseSettings(BaseModel):
    def get_nested_value(self, key: str) -> Any:
        obj: Any = self

        for part in key.split("."):
            obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)

        return obj

    @staticmethod
    def set_nested_value(data: dict[str, Any], key: str, value: Any) -> None:
        parts = key.split(".")
        for part in parts[:-1]:
            data = data.setdefault(part, {})
        data[parts[-1]] = value


PluginsType = Annotated[
    dict[str, dict[str, Any] | BaseModel],
    PlainSerializer(
        lambda d: {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in d.items()},
        return_type=dict[str, dict[str, Any]],
    ),
]


class GlobalSettings(BaseSettings):
    """
    Application-wide settings stored in the package directory.

    These settings apply to all scripts and workspaces.
    """

    __section__ = "General"
    model_config = ConfigDict(ignored_types=(classproperty, classproperty.cached))

    shortcuts: list[ShortcutConfig] = Field(
        default_factory=lambda: [
            ShortcutConfig(action_id=action, key_sequence=action.definition.default_key) for action in ActionID
        ]
    )

    autosave: Annotated[
        time,
        WidgetTimeEdit(
            label="Settings auto save interval",
            min=QTime(),
            max=QTime(0, 30, 0, 0),
            display_format="mm:ss",
            tooltip="The interval for the auto save timer of both global and local settings in minutes",
            to_ui=lambda tm, _qtime_cls=QTime: _qtime_cls(0, tm.minute, tm.second, 0),
            from_ui=lambda qtime: qtime.toPython(),
        ),
    ] = time(0, 2, 0, 0)

    status_message_timeout: Annotated[
        int,
        DoubleSpin(
            label="Message timeout",
            min=0,
            max=1_000_000,
            suffix=" s",
            decimals=3,
            to_ui=lambda v: v / 1000.0,
            from_ui=lambda v: int(v * 1000.0),
            tooltip="Duration of status messages",
        ),
    ] = 5000

    chdir: Annotated[
        bool,
        Checkbox(
            label="Change directory",
            text="Change working directory on script load",
            tooltip="Change the current working directory to the script's directory upon loading.\n\n"
            "Note:\n"
            "The working directory is a process-level attribute.\n"
            "Changing it is not thread-safe and may cause unexpected behavior\n"
            "if background tasks attempt to access files using relative paths.\n"
            "Leave this disabled unless a script explicitly relies on the working directory.",
        ),
    ] = False

    appearance: AppearanceSettings = AppearanceSettings()
    timeline: TimelineSettings = TimelineSettings()
    playback: PlaybackSettings = PlaybackSettings()
    view: ViewSettings = ViewSettings()

    plugins: PluginsType = Field(default_factory=dict)

    # Hidden
    window_geometry: WindowGeometry = WindowGeometry()
    view_tools: ViewTools = ViewTools()
    qt_settings: QtSettings = QtSettings()

    def get_key(self, action_id: str) -> str:
        """Get the key sequence for a specific action."""
        return next((s.key_sequence for s in self.shortcuts if s.action_id == action_id), "")

    @classproperty.cached
    @classmethod
    def config_path(cls) -> SPath:
        return SPath(
            user_config_path(
                "vsview",
                appauthor=False,
                roaming=getenv_bool("VSVIEW_GLOBAL_SETTINGS_ROAMING"),
                ensure_exists=True,
            )
        )

    @classproperty.cached
    @classmethod
    def path(cls) -> SPath:
        r"""
        Get the global settings path.

        Default locations:
        - Windows: `%LOCALAPPDATA%\vsview\global_settings.json`
        - Linux:   `~/.config/vsview/global_settings.json`
        - macOS:   `~/Library/Application Support/vsview/global_settings.json`

        If the `VSVIEW_GLOBAL_SETTINGS_ROAMING` environment variable is set (Windows only),
        it uses: `%APPDATA%\vsview\global_settings.json`
        """
        return cls.config_path / "global_settings.json"

    @classproperty.cached
    @classmethod
    def path_env(cls) -> SPath:
        r"""
        Get the scoped global settings path.

        If the `VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT` environment variable is set,
        the path is scoped to the current Python environment using the environment's
        parent directory name and the executable's modification timestamp:
        `{base_config_path}\{env_parent_name}\{executable_mtime_ns}\global_settings.json`
        """
        if getenv_bool("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT") and sys.executable:
            env_dir = Path(sys.prefix)

            # We are inside a virtual environment
            if sys.prefix != sys.base_prefix:
                env_dir = env_dir.parent

            return cls.config_path / env_dir.name / str(os.stat(sys.executable).st_mtime_ns) / "global_settings.json"

        return cls.path


def fallback_global(attr: str) -> Callable[[Any | None], Any]:
    """Fallback to global settings if the value is None."""

    getter = attrgetter(attr)

    def validator(v: Any | None) -> Any:
        if v is not None:
            return v
        from .manager import SettingsManager

        return getter(SettingsManager.global_settings)

    return validator


class LocalPlaybackSettings(BaseModel):
    seek_step: Annotated[
        int,
        BeforeValidator(fallback_global("timeline.seek_step")),
    ] = Field(default=None, validate_default=True)
    speed: float = 1.0
    uncapped: bool = False
    zone_frames: int = 100
    loop: bool = False
    step: int = 1

    last_audio_index: Annotated[int, AfterValidator(lambda i: max(0, i))] = 0
    current_volume: float = 0.5
    muted: bool = False
    audio_delay: Annotated[
        float,
        BeforeValidator(fallback_global("playback.audio_delay")),
    ] = Field(default=None, validate_default=True)


class LocalTimelineSettings(BaseModel):
    mode: Annotated[
        Literal["frame", "time"],
        BeforeValidator(fallback_global("timeline.mode")),
    ] = Field(default=None, validate_default=True)


class SynchronizationSettings(BaseModel):
    __section__ = "Synchronization"

    sync_playhead: Annotated[
        int,
        Checkbox(
            label="Sync Playhead",
            text="Sync playhead across outputs",
            tooltip="Sync playhead across outputs",
        ),
    ] = 1
    sync_zoom: Annotated[
        bool,
        Checkbox(
            label="Sync Zoom",
            text="Sync zoom level across outputs",
            tooltip="Sync zoom level across outputs",
        ),
    ] = True
    sync_scroll: Annotated[
        bool,
        Checkbox(
            label="Sync Scroll",
            text="Sync scroll position across outputs",
            tooltip="Sync scroll position across outputs",
        ),
    ] = True
    autofit_all_views: Annotated[
        bool,
        Checkbox(
            label="Auto fit",
            text="Enable autofit on all views",
            tooltip="Enable autofit on all views",
        ),
    ] = False


class LayoutSettings(BaseModel):
    """Layout settings for plugin splitter and dock widgets."""

    plugin_splitter_sizes: list[int] | None = None
    """Splitter sizes from QSplitter.sizes()"""

    plugin_tab_index: int = 0
    """Currently selected plugin tab index"""

    dock_state: str | None = None
    """Base64-encoded QMainWindow.saveState() byte array for dock positions"""


class LocalSettings(BaseSettings):
    """
    Per-script settings stored in the .vsjet directory.

    These settings are specific to a single script file.
    """

    __section__ = "General"

    source_path: str = ""
    last_frame: int = 0
    last_time: timedelta = timedelta()
    last_output_tab_index: Annotated[int, AfterValidator(lambda i: max(0, i))] = 0
    playback: LocalPlaybackSettings = Field(default_factory=lambda: LocalPlaybackSettings())
    timeline: LocalTimelineSettings = Field(default_factory=lambda: LocalTimelineSettings())
    synchronization: SynchronizationSettings = Field(default_factory=lambda: SynchronizationSettings())
    layout: LayoutSettings = Field(default_factory=lambda: LayoutSettings())
    plugins: PluginsType = Field(default_factory=dict)
