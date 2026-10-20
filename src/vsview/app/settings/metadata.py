from __future__ import annotations

import inspect
import sys
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import KW_ONLY, dataclass, field
from functools import cache, wraps
from itertools import chain
from logging import getLogger
from typing import TYPE_CHECKING, Any, Concatenate, get_args, get_origin, override

from jetpytools import CustomTypeError, SupportsRichComparison
from pydantic import TypeAdapter, ValidationError
from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from ..utils import get_all_original_bases
from .secrets import SecretsManager
from .widgets import (
    ColorPickerInput,
    CustomSpinBox,
    FilePickerWidget,
    ListEditWidget,
    LoginCredentialsInput,
    PathListEditWidget,
)

if sys.version_info >= (3, 13):
    from typing import TypeIs
else:
    from typing_extensions import TypeIs

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
    def apply_transform(self, value: Any, transform: Callable[[Any], Any] | None) -> Generator[Any]:
        if transform:
            try:
                yield transform(value)
            except Exception as e:
                logger.error("Failed to convert value: %r with error: %s", value, e)
                raise
        else:
            yield value

    @classmethod
    @cache
    def get_widget_type(cls) -> W | None:
        all_bases = get_all_original_bases(cls)

        for arg in chain.from_iterable(get_args(b) for b in all_bases):
            arg = get_origin(arg) or arg
            if isinstance(arg, type) and issubclass(arg, QWidget):
                return arg  # type: ignore[return-value]
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class WidgetMetadataWithDependency[W: QWidget](WidgetMetadata[W]):
    depends_on: str | None = None
    """Key of the setting this widget depends on (e.g., 'appearance.theme')."""

    @abstractmethod
    def populate(self, widget: W, parent_value: Any | None = None) -> None: ...


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

    @override
    def create_widget(self, parent: QWidget | None = None) -> LoginCredentialsInput:
        return LoginCredentialsInput(parent)

    @override
    def load_value(self, widget: LoginCredentialsInput, value: str) -> None:
        username = value
        password = ""

        if username:
            password = SecretsManager.get(self.namespace, self.context, username) or ""
        elif cred := SecretsManager.get_credential(self.namespace, self.context):
            username = cred.username
            password = cred.password or ""

        widget.credentials = (username or "", password or "")
        widget.setProperty("_old_username", username or "")

    @override
    def get_value(self, widget: LoginCredentialsInput) -> Any:
        username, password = widget.credentials
        old_username = widget.property("_old_username")

        if old_username and old_username != username:
            SecretsManager.delete(self.namespace, self.context, old_username)

        if username and password:
            SecretsManager.set(self.namespace, self.context, username, password)

        widget.setProperty("_old_username", username)
        return username


@dataclass(frozen=True, slots=True)
class SecretLineEdit(WidgetMetadata[QLineEdit]):
    """Secret LineEdit widget metadata stored in SecretsManager."""

    namespace: str
    """Namespace for the secret."""
    context: str
    """Context for the secret."""
    key: str
    """Key for the secret."""

    _: KW_ONLY
    echo_mode: QLineEdit.EchoMode = QLineEdit.EchoMode.PasswordEchoOnEdit
    """Echo mode for the LineEdit widget."""
    placeholder_text: str | None = None
    """Placeholder text for the LineEdit widget."""
    strip: bool = True
    """Whether to strip leading and trailing whitespace from the secret."""
    to_ui: None = None
    from_ui: None = None

    @override
    def create_widget(self, parent: QWidget | None = None) -> QLineEdit:
        return QLineEdit(parent, echoMode=self.echo_mode, placeholderText=self.placeholder_text)

    @override
    def load_value(self, widget: QLineEdit, value: Any) -> None:
        secret = SecretsManager.get(self.namespace, self.context, self.key) or ""
        widget.setText(secret)

    @override
    def get_value(self, widget: QLineEdit) -> Any:
        secret = widget.text().strip() if self.strip else widget.text()

        if secret:
            SecretsManager.set(self.namespace, self.context, self.key, secret)
        else:
            SecretsManager.delete(self.namespace, self.context, self.key)

        return None


@dataclass(frozen=True, slots=True)
class ColorPicker(WidgetMetadata[ColorPickerInput]):
    """ColorPicker widget metadata."""

    @override
    def create_widget(self, parent: QWidget | None = None) -> ColorPickerInput:
        return ColorPickerInput(parent)

    @override
    def load_value(self, widget: ColorPickerInput, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            if isinstance(v, str):
                widget.color = QColor(v)
            elif isinstance(v, QColor):
                widget.color = v

    @override
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

    @override
    def create_widget(self, parent: QWidget | None = None) -> QCheckBox:
        return QCheckBox(self.text, parent, tristate=self.tristate)

    @override
    def load_value(self, widget: QCheckBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            if self.tristate:
                if isinstance(v, bool):
                    widget.setCheckState(Qt.CheckState.Checked if v else Qt.CheckState.Unchecked)
                else:
                    widget.setCheckState(Qt.CheckState(v))
            else:
                widget.setChecked(bool(v))

    @override
    def get_value(self, widget: QCheckBox) -> Any:
        val = widget.checkState() if self.tristate else widget.isChecked()
        with self.apply_transform(val, self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class LineEdit(WidgetMetadata[QLineEdit]):
    """LineEdit widget metadata."""

    default_value: Any = field(default=None, kw_only=True)

    @override
    def create_widget(self, parent: QWidget | None = None) -> QLineEdit:
        return QLineEdit(parent)

    @override
    def load_value(self, widget: QLineEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setText(v)

    @override
    def get_value(self, widget: QLineEdit) -> Any:
        with self.apply_transform(widget.text(), self.from_ui) as value:
            return self.default_value if not value and self.default_value is not None else value


@dataclass(frozen=True, slots=True)
class Dropdown(WidgetMetadataWithDependency[QComboBox]):
    """Dropdown/ComboBox widget metadata."""

    items: (
        Sequence[tuple[str, Any]]
        | Callable[[Any], Sequence[tuple[str, Any]]]
        | Callable[[], Sequence[tuple[str, Any]]]  # no format
    )
    """
    Iterable of (display_text, value) tuples or a callable that returns one.
    If `default_value` is specified the items must be a Sequence or a callable that takes one parameter.
    """

    _: KW_ONLY
    default_value: Any = None
    """Default value to return when unselected (currentIndex == -1) or fallback."""

    @override
    def create_widget(self, parent: QWidget | None = None) -> QComboBox:
        widget = QComboBox(parent)
        for display_text, value in self._resolve_items():
            widget.addItem(display_text, value)
        return widget

    @override
    def load_value(self, widget: QComboBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setCurrentIndex(widget.findData(v))

    @override
    def get_value(self, widget: QComboBox) -> Any:
        if widget.currentIndex() == -1:
            return self.default_value
        with self.apply_transform(widget.currentData(), self.from_ui) as value:
            return value

    @override
    def populate(self, widget: QComboBox, parent_value: Any | None = None) -> None:
        widget.clear()

        for display_text, value in self._resolve_items(parent_value):
            widget.addItem(display_text, value)

        if (idx := widget.findData(self.default_value)) >= 0:
            widget.setCurrentIndex(idx)
        else:
            widget.setCurrentIndex(0)

    def _resolve_items(self, parent_value: Any | None = None) -> Sequence[tuple[str, Any]]:
        if not callable(self.items):
            return self.items

        sig = inspect.signature(self.items)

        def is_callable_0_param[R](cb: Callable[..., R]) -> TypeIs[Callable[[], R]]:
            return len(sig.parameters) == 0

        def is_callable_1_param[R](cb: Callable[..., R]) -> TypeIs[Callable[[Any], R]]:
            return len(sig.parameters) == 1

        if self.depends_on is None and is_callable_0_param(self.items):
            return self.items()

        if self.depends_on and is_callable_1_param(self.items):
            return self.items(parent_value)

        raise CustomTypeError(
            f"items callable accepts {len(sig.parameters)} argument(s), but 1 is required when depends_on is specified",
            func=f"{self.__class__.__name__}.{self.label}",
        )


@dataclass(frozen=True, slots=True)
class Spin(WidgetMetadata[QSpinBox]):
    """SpinBox widget metadata for integers."""

    min: int = 0
    max: int = 100
    suffix: str = ""
    min_text: str | None = None
    max_text: str | None = None

    @override
    def create_widget(self, parent: QWidget | None = None) -> QSpinBox:
        if self.min_text is not None or self.max_text is not None:
            widget = CustomSpinBox(
                parent,
                min_text=self.min_text,
                max_text=self.max_text,
                custom_suffix=self.suffix,
            )
            widget.setMinimum(self.min)
            widget.setMaximum(self.max)
            return widget

        widget = QSpinBox(parent)
        widget.setMinimum(self.min)
        widget.setMaximum(self.max)
        widget.setSuffix(self.suffix)
        return widget

    @override
    def load_value(self, widget: QSpinBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setValue(v)

    @override
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

    @override
    def create_widget(self, parent: QWidget | None = None) -> QDoubleSpinBox:
        widget = QDoubleSpinBox(parent)
        widget.setMinimum(self.min)
        widget.setMaximum(self.max)
        widget.setSuffix(self.suffix)
        widget.setDecimals(self.decimals)
        widget.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
        return widget

    @override
    def load_value(self, widget: QDoubleSpinBox, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setValue(v)

    @override
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

    @override
    def create_widget(self, parent: QWidget | None = None) -> QPlainTextEdit:
        widget = QPlainTextEdit(parent)
        widget.setMinimumHeight(self.max_height)
        widget.setMaximumHeight(self.max_height)
        return widget

    @override
    def load_value(self, widget: QPlainTextEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setPlainText(str(v))

    @override
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

    @override
    def create_widget(self, parent: QWidget | None = None) -> ListEditWidget[T]:
        return ListEditWidget(self.value_type, parent, self.default_value, self.dialog_label_text, self.completions)

    @override
    def load_value(self, widget: ListEditWidget[T], value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.set_values(v)

    @override
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

    @override
    def create_widget(self, parent: QWidget | None = None) -> QTimeEdit:
        widget = QTimeEdit(parent)

        if self.min:
            widget.setMinimumTime(self.min)
        if self.max:
            widget.setMaximumTime(self.max)

        if self.display_format:
            widget.setDisplayFormat(self.display_format)

        return widget

    @override
    def load_value(self, widget: QTimeEdit, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.setTime(QTime(v))

    @override
    def get_value(self, widget: QTimeEdit) -> Any:
        with self.apply_transform(widget.time(), self.from_ui) as value:
            return value


@dataclass(frozen=True, slots=True)
class PathListEdit(WidgetMetadata[PathListEditWidget]):
    """Widget metadata for PathListEditWidget (directory paths list)."""

    default_value: str | Sequence[str] | None = field(default=None, kw_only=True)

    @override
    def create_widget(self, parent: QWidget | None = None) -> PathListEditWidget:
        return PathListEditWidget(parent, self.default_value)

    @override
    def load_value(self, widget: PathListEditWidget, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.set_values(v)

    @override
    def get_value(self, widget: PathListEditWidget) -> Any:
        with self.apply_transform(widget.get_values(), self.from_ui) as value:
            return sorted(set(value)) if isinstance(value, list) else value


@dataclass(frozen=True, slots=True)
class FilePicker(WidgetMetadata[FilePickerWidget]):
    """Widget metadata for FilePickerWidget (single file picker)."""

    file_filter: str = "All Files (*.*)"
    dialog_title: str = "Select File"

    @override
    def create_widget(self, parent: QWidget | None = None) -> FilePickerWidget:
        return FilePickerWidget(parent, self.file_filter, self.dialog_title)

    @override
    def load_value(self, widget: FilePickerWidget, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as v:
            widget.file_path = v or ""

    @override
    def get_value(self, widget: FilePickerWidget) -> Any:
        with self.apply_transform(widget.file_path, self.from_ui) as value:
            return value or None
