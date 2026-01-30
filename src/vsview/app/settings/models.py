"""Settings models for vsview."""

from __future__ import annotations

import os
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import KW_ONLY, dataclass, field
from datetime import time
from enum import StrEnum
from functools import wraps
from logging import getLogger
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

from jetpytools import SupportsRichComparison
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_serializer, field_validator
from PySide6.QtCore import QTime
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QPlainTextEdit, QSpinBox, QTimeEdit, QWidget

from .enums import Resizer

logger = getLogger(__name__)

os.environ["PYDANTIC_ERRORS_INCLUDE_URL"] = "false"


class WidgetMetadataMeta(ABCMeta):
    """Metaclass for WidgetMetadata."""

    def __new__[MetaSelf: WidgetMetadataMeta](
        mcls: type[MetaSelf], name: str, bases: tuple[type, ...], namespace: dict[str, Any], /, **kwargs: Any
    ) -> MetaSelf:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)

        func = getattr(cls, "create_widget")

        if not getattr(func, "__has_tooltip_deco__", False):
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

        setattr(wrapper, "__has_tooltip_deco__", True)

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


@dataclass(frozen=True, slots=True)
class Checkbox(WidgetMetadata[QCheckBox]):
    """Checkbox widget metadata."""

    text: str
    """Text displayed next to the checkbox."""

    def create_widget(self, parent: QWidget | None = None) -> QCheckBox:
        widget = QCheckBox(parent)
        widget.setText(self.text)
        return widget

    def load_value(self, widget: QCheckBox, value: Any) -> None:
        if self.to_ui:
            value = self.to_ui(value)
        widget.setChecked(not not value)  # noqa: SIM208

    def get_value(self, widget: QCheckBox) -> Any:
        value = widget.isChecked()
        return self.from_ui(value) if self.from_ui else value


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
        if self.to_ui:
            value = self.to_ui(value)
        index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)

    def get_value(self, widget: QComboBox) -> Any:
        value = widget.currentData()
        return self.from_ui(value) if self.from_ui else value


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
        if self.to_ui:
            value = self.to_ui(value)
        widget.setValue(value)

    def get_value(self, widget: QSpinBox) -> Any:
        value = widget.value()
        return self.from_ui(value) if self.from_ui else value


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
        return widget

    def load_value(self, widget: QDoubleSpinBox, value: Any) -> None:
        if self.to_ui:
            value = self.to_ui(value)
        widget.setValue(value)

    def get_value(self, widget: QDoubleSpinBox) -> Any:
        value = widget.value()
        return self.from_ui(value) if self.from_ui else value


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
        if self.to_ui:
            value = self.to_ui(value)
        widget.setPlainText(str(value))

    def get_value(self, widget: QPlainTextEdit) -> Any:
        value = widget.toPlainText()
        return self.from_ui(value) if self.from_ui else value

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
class TimeEdit(WidgetMetadata[QTimeEdit]):
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
        if self.to_ui:
            value = self.to_ui(value)
        widget.setTime(QTime(value))

    def get_value(self, widget: QTimeEdit) -> Any:
        value = widget.time()
        return self.from_ui(value) if self.from_ui else value


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
    SAVE_CURRENT_IMAGE = "workspace.loader.view.save_current_image", "Save Current Image", "Ctrl+Shift+S"
    COPY_IMAGE_TO_CLIPBOARD = "workspace.loader.view.copy_image_to_clipboard", "Copy Image to Clipboard", "Ctrl+S"

    # Timeline actions
    COPY_CURRENT_FRAME = "workspace.loader.timeline.copy_current_frame", "Copy Current Frame", "S"
    COPY_CURRENT_TIME = "workspace.loader.timeline.copy_current_time", "Copy Current Time", "Shift+S"
    PLAY_PAUSE = "workspace.loader.timeline.play_pause", "Play / Pause", "Space"
    SEEK_PREVIOUS_FRAME = "workspace.loader.timeline.seek_previous_frame", "Seek Previous Frame", "Left"
    SEEK_NEXT_FRAME = "workspace.loader.timeline.seek_next_frame", "Seek Next Frame", "Right"
    SEEK_N_FRAMES_BACK = "workspace.loader.timeline.seek_n_frames_back", "Seek N Frames Back", "Shift+Left"
    SEEK_N_FRAMES_FORWARD = "workspace.loader.timeline.seek_n_frames_forward", "Seek N Frames Forward", "Shift+Right"

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

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(value, label, default_key)
        return obj


class ShortcutConfig(BaseModel):
    """Configuration for a single keyboard shortcut."""

    action_id: str
    key_sequence: str

    @field_validator("key_sequence")
    @classmethod
    def validate_sequence(cls, v: str) -> str:
        seq = QKeySequence(v)

        if seq.isEmpty() and v:  # Allow empty for unassigned, but not invalid garbage
            raise ValueError(f"Invalid key sequence: {v}")

        return v


class AppearanceSettings(BaseModel):
    """Settings for the application appearance."""

    __section__ = "Appearance"

    icon_provider: Annotated[
        str,
        Dropdown(
            label="Icon Provider",
            items=[],  # Populated dynamically from providers registry
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
            items=[],  # Populated dynamically from Pygments styles
            tooltip="Theme for the editor of the Quick Script workspace",
        ),
    ] = "gruvbox-dark"


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
            max=10.0,
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

    zoom_factors: Annotated[
        list[float],
        PlainTextEdit(
            label="Zoom Factors",
            value_type=float,
            max_height=100,
            tooltip="Zoom factors for the views\nPut one factor per line",
        ),
    ] = [0.25, 0.5, 0.75, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0]


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

    @field_serializer("plugins", check_fields=False)
    def _serialize_plugins(self, value: dict[str, dict[str, Any] | BaseModel]) -> dict[str, dict[str, Any]]:
        return {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in value.items()}


class GlobalSettings(BaseSettings):
    """
    Application-wide settings stored in the package directory.

    These settings apply to all scripts and workspaces.
    """

    __section__ = "General"

    shortcuts: list[ShortcutConfig] = Field(
        default_factory=lambda: [
            ShortcutConfig(action_id=action, key_sequence=action.definition.default_key) for action in ActionID
        ]
    )

    autosave: Annotated[
        time,
        TimeEdit(
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

    appearance: AppearanceSettings = AppearanceSettings()
    timeline: TimelineSettings = TimelineSettings()
    playback: PlaybackSettings = PlaybackSettings()
    view: ViewSettings = ViewSettings()

    plugins: dict[str, dict[str, Any] | BaseModel] = Field(default_factory=dict)

    # Hidden
    window_geometry: WindowGeometry = WindowGeometry()
    view_tools: ViewTools = ViewTools()

    def get_key(self, action_id: str) -> str:
        """Get the key sequence for a specific action."""
        return next((s.key_sequence for s in self.shortcuts if s.action_id == action_id), "")


class LocalPlaybackSettings(BaseModel):
    seek_step_raw: int | None = None
    speed: float = 1.0
    uncapped: bool = False
    zone_frames: int = 100
    loop: bool = False

    last_audio_index: int | None = None
    current_volume: float = 0.5
    muted: bool = False
    audio_delay_raw: float | None = None

    @property
    def seek_step(self) -> int:
        from .manager import SettingsManager

        return (
            self.seek_step_raw if self.seek_step_raw is not None else SettingsManager.global_settings.timeline.seek_step
        )

    @seek_step.setter
    def seek_step(self, value: int | None) -> None:
        self.seek_step_raw = value

    @property
    def audio_delay(self) -> float:
        from .manager import SettingsManager

        return (
            self.audio_delay_raw
            if self.audio_delay_raw is not None
            else SettingsManager.global_settings.playback.audio_delay
        )

    @audio_delay.setter
    def audio_delay(self, value: float | None) -> None:
        self.audio_delay_raw = value


class SynchronizationSettings(BaseModel):
    __section__ = "Synchronization"

    sync_playhead: Annotated[
        bool,
        Checkbox(
            label="Sync Playhead",
            text="Sync playhead across outputs",
            tooltip="Sync playhead across outputs",
        ),
    ] = True
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
    last_output_tab_index: int = 0
    playback: LocalPlaybackSettings = LocalPlaybackSettings()
    synchronization: SynchronizationSettings = SynchronizationSettings()
    layout: LayoutSettings = LayoutSettings()
    plugins: dict[str, dict[str, Any] | BaseModel] = Field(default_factory=dict)


# Global settings file location is inside the package directory
GLOBAL_SETTINGS_PATH = Path(__file__).parent.parent.parent / "global_settings.json"
DEFAULT_GLOBAL_SETTINGS = GlobalSettings()
DEFAULT_LOCAL_SETTINGS = LocalSettings()
