"""Settings models for vsview."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import time, timedelta
from logging import getLogger
from operator import attrgetter
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, get_args, get_origin, get_type_hints, override

import vapoursynth as vs
from jetpytools import SPath, classproperty
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
    model_serializer,
)
from pygments.styles import get_all_styles as get_pygments_styles
from PySide6.QtCore import QPointF, Qt, QTime
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QColorDialog, QStyleFactory, QWidget

from ...assets import ICON_PROVIDERS
from ...env import getenv_bool
from .action import ActionID
from .enums import Resizer
from .metadata import (
    Checkbox,
    ColorPicker,
    DoubleSpin,
    Dropdown,
    FilePicker,
    LineEdit,
    PathListEdit,
    Spin,
    WidgetMetadata,
    WidgetTimeEdit,
)

logger = getLogger(__name__)


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


def extract_settings(
    model: type[BaseModel],
    prefix: str = "",
    section: str | None = None,
    section_prefix: str | None = None,
) -> list[SettingEntry]:
    """Extract SettingEntry list from a Pydantic model's Annotated fields."""
    result = list[SettingEntry]()
    hints = get_type_hints(model, include_extras=True)

    if model_section_attr := getattr(model, "__section__", None):
        model_section = f"{section_prefix} - {model_section_attr}" if section_prefix else model_section_attr
    else:
        model_section = section or section_prefix

    for field_name, annotation in hints.items():
        key = f"{prefix}{field_name}" if prefix else field_name
        metadata = _get_widget_metadata(annotation)

        if metadata is None:
            # Check if it's a nested BaseModel
            inner_type = get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation
            if isinstance(inner_type, type) and issubclass(inner_type, BaseModel):
                result.extend(
                    extract_settings(
                        inner_type,
                        prefix=f"{key}.",
                        section=model_section,
                        section_prefix=section_prefix,
                    )
                )
        else:
            if model_section is None:
                raise ValueError(f"No section defined for setting '{key}'. Add __section__ to the model class.")
            result.append(SettingEntry(key=key, section=model_section, metadata=metadata))

    return result


# -----------------------------------------------------------------------------------


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
                "Application theme.\n"  # no fmt
                "You may have to restart the application for the changes to fully take effect."
            ),
        ),
    ] = None

    style: Annotated[
        str | None,
        Dropdown(
            label="Style",
            items=lambda QStyleFactory=QStyleFactory: [(k.title(), k.lower()) for k in QStyleFactory.keys()],  # noqa: N803, SIM118
            tooltip=(
                "Application style.\n"  # no fmt
                "You may have to restart the application for the changes to fully take effect."
            ),
        ),
    ] = None
    custom_stylesheet: Annotated[
        str | None,
        FilePicker(
            label="Custom Stylesheet",
            file_filter="Stylesheets (*.qss)",
            dialog_title="Select QSS File",
            tooltip="Path to a custom .qss stylesheet to apply.",
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
    ] = Qt.CheckState.Checked

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

    output_transfer: Annotated[
        vs.TransferCharacteristics,
        Dropdown(
            label="Output Transfer",
            items=[
                ("BT709", vs.TransferCharacteristics.TRANSFER_BT709),
                ("Linear", vs.TransferCharacteristics.TRANSFER_LINEAR),
                ("BT2020_10", vs.TransferCharacteristics.TRANSFER_BT2020_10),
                ("ST2084 (PQ)", vs.TransferCharacteristics.TRANSFER_ST2084),
                ("ARIB_B67 (HLG)", vs.TransferCharacteristics.TRANSFER_ARIB_B67),
            ],
            tooltip=(
                "The target transfer characteristics used when converting SDR content to RGB.\n"
                "Can be overridden on a per-clip basis via set_output() keyword arguments."
            ),
        ),
    ] = vs.TransferCharacteristics.TRANSFER_BT709

    output_primaries: Annotated[
        vs.ColorPrimaries,
        Dropdown(
            label="Output Primaries",
            items=[
                ("BT709", vs.ColorPrimaries.PRIMARIES_BT709),
                ("BT2020", vs.ColorPrimaries.PRIMARIES_BT2020),
                ("DCI-P3 (ST432_1)", vs.ColorPrimaries.PRIMARIES_ST432_1),
                ("DCI-P3 (ST431_2)", vs.ColorPrimaries.PRIMARIES_ST431_2),
            ],
            tooltip=(
                "The target color primaries used when converting SDR content to RGB.\n"
                "Can be overridden on a per-clip basis via set_output() keyword arguments."
            ),
        ),
    ] = vs.ColorPrimaries.PRIMARIES_BT709

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
    model_config = ConfigDict(validate_assignment=True)

    custom_colors: list[
        Annotated[
            QColor,
            PlainValidator(lambda v: v if isinstance(v, QColor) else QColor(v)),
            PlainSerializer(lambda color: color.name(), return_type=str),
        ]
    ] = Field(default_factory=list)

    custom_plugin_paths: Annotated[
        list[str],
        PathListEdit(
            label="Custom plugins paths",
            default_value=[],
            tooltip="Directories to search for custom Qt plugins.",
        ),
    ] = Field(default_factory=list)

    @override
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

    loaded_page_disable_duration: Annotated[
        int,
        Spin(
            label="Loaded page disable duration",
            min=0,
            max=500,
            suffix=" ms",
            min_text="Immediately",
            max_text="∞",
            tooltip=(
                "The duration in milliseconds to wait before disabling the page if a frame takes too long to render.\n"
                "Values of 500 ms or more will not disable the page at all."
            ),
        ),
    ] = 50

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


class LocalViewSettings(BaseModel):
    """View-specific settings, stored per script."""

    last_zoom: float | None = None
    """The last zoom factor applied to the view."""

    last_center: Annotated[
        QPointF | None,
        PlainValidator(lambda v: QPointF(*v) if v else v),
        PlainSerializer(lambda v: v.toTuple() if isinstance(v, QPointF) else None),
    ] = None
    """The last center position of the view."""


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
    playback: LocalPlaybackSettings = Field(default_factory=LocalPlaybackSettings)  # pyright: ignore[reportArgumentType]
    timeline: LocalTimelineSettings = Field(default_factory=LocalTimelineSettings)  # pyright: ignore[reportArgumentType]
    synchronization: SynchronizationSettings = Field(default_factory=SynchronizationSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)
    view: LocalViewSettings = Field(default_factory=LocalViewSettings)
    plugins: PluginsType = Field(default_factory=dict)
