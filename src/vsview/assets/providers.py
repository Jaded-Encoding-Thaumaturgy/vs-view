"""
Icon provider abstraction for vsview.

Provides a unified interface for loading icons from different providers with normalized icon names.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from importlib import resources
from importlib.resources.abc import Traversable
from typing import ClassVar, Self, override

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPixmap

from .utils import load_svg


class IconName(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    FAST_FORWARD = "fast-forward"
    REWIND = "rewind"
    SKIP_FORWARD = "skip-forward"
    SKIP_BACK = "skip-back"
    CHECK = "check"
    SPINNER_GAP = "spinner-gap"
    UNLINK = "link-break"
    LINK = "link"
    LINK_2 = "link-simple-horizontal"
    LINK_3 = "link-simple-horizontal-break"
    MAGNIFYING_GLASS = "magnifying-glass"
    FRAME_CORNERS = "frame-corners"
    ARROWS_OUT_CARDINAL = "arrows-out-cardinal"
    ARROW_U_TOP_LEFT = "arrow-u-up-left"
    X_CIRCLE = "x-circle"
    FILE_TEXT = "file-text"
    FILE_VIDEO = "file-video"
    FILE_CODE = "file-code"
    SAVE = "floppy-disk"
    ARROW_LEFT = "arrow-left"
    ARROW_RIGHT = "arrow-right"
    ARROW_CLOCKWISE = "arrow-clockwise"
    ARROW_COUNTER_CLOCKWISE = "arrow-counter-clockwise"
    # Audio icons
    VOLUME_HIGH = "speaker-high"
    VOLUME_MID = "speaker-low"
    VOLUME_LOW = "speaker-none"
    VOLUME_OFF = "speaker-x"
    VOLUME_MUTE = "speaker-slash"
    PIPETTE = "eyedropper"
    CLIPBOARD = "clipboard"
    PLUS = "plus"
    MINUS = "minus"
    FILE_IMPORT = "file-arrow-down"
    FILE_EXPORT = "file-arrow-up"
    # Scening icons
    SCENE_ADD = "selection-plus"
    MARK_IN = "selection-background"
    MARK_OUT = "selection-foreground"
    FRAME_ADD = "selection"
    SIDEBAR_RIGHT = "sidebar-simple-mirror", "mirror"
    SIDEBAR_LEFT = "sidebar-simple"

    modifier: str

    def __new__(cls, value: str, modifier: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.modifier = modifier
        return obj


class IconProvider(ABC):
    """Abstract base class for icon providers."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Provider identifier used in settings."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name for UI."""

    @property
    @abstractmethod
    def weights(self) -> frozenset[str]:
        """Available weight variants for this provider."""

    @property
    @abstractmethod
    def default_weight(self) -> str:
        """Default weight for this provider."""

    @abstractmethod
    def get_folder(self) -> Traversable:
        """Get the assets folder for this provider."""

    @abstractmethod
    def map_name(self, name: IconName) -> str:
        """Map normalized IconName to provider-specific filename (without extension)."""

    def get_icon_path(self, name: IconName, weight: str) -> Traversable:
        """Get the path to a specific icon file, with fallback to default weight."""
        mapped = self.map_name(name)
        folder = self.get_folder()

        # Try requested weight first
        if weight != self.default_weight:
            filename = f"{mapped}-{weight}.svg"
            path = folder / filename
            # Fallback to default weight if variant doesn't exist
            if not path.is_file():
                path = folder / f"{mapped}.svg"
            return path

        return folder / f"{mapped}.svg"

    def get_pixmap(
        self,
        name: IconName,
        weight: str,
        size: QSize,
        color: QColor | None = None,
        dpr: float | None = None,
    ) -> QPixmap:
        """Get a QPixmap from the specified IconName."""
        return load_svg(self.get_icon_path(name, weight).read_bytes(), size, color, dpr)


class PhosphorProvider(IconProvider):
    """Phosphor Icons provider."""

    @property
    @override
    def id(self) -> str:
        return "phosphor"

    @property
    @override
    def name(self) -> str:
        return "Phosphor Icons"

    @property
    @override
    def weights(self) -> frozenset[str]:
        return frozenset({"thin", "light", "regular", "bold", "fill", "duotone"})

    @property
    @override
    def default_weight(self) -> str:
        return "regular"

    @override
    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.phosphor")

    @override
    def map_name(self, name: IconName) -> str:
        # Phosphor uses same kebab-case names as IconName
        return name.value.removesuffix(f"-{name.modifier}")

    @override
    def get_pixmap(
        self,
        name: IconName,
        weight: str,
        size: QSize,
        color: QColor | None = None,
        dpr: float | None = None,
    ) -> QPixmap:
        """Get a QPixmap from the specified IconName."""

        svg = super().get_pixmap(name, weight, size, color, dpr)

        if not name.modifier:
            return svg

        match name.modifier:
            case "mirror":
                img = svg.toImage()
                img.mirror(horizontally=True)
                svg = QPixmap(img)
            case _:
                raise NotImplementedError

        return svg


class MaterialProvider(IconProvider):
    """Material Design Icons provider."""

    # Mapping from normalized IconName to Material icon names
    NAME_MAP: ClassVar[dict[IconName, str]] = {
        IconName.SKIP_FORWARD: "skip-next",
        IconName.SKIP_BACK: "skip-previous",
        IconName.SPINNER_GAP: "loading",
        IconName.UNLINK: "link-variant-off",
        IconName.LINK: "link-variant",
        IconName.LINK_2: "link-variant-plus",
        IconName.LINK_3: "link-variant-minus",
        IconName.MAGNIFYING_GLASS: "magnify",
        IconName.FRAME_CORNERS: "border-radius",
        IconName.ARROWS_OUT_CARDINAL: "arrow-expand-all",
        IconName.ARROW_U_TOP_LEFT: "arrow-u-left-top",
        IconName.ARROW_CLOCKWISE: "reload",
        IconName.ARROW_COUNTER_CLOCKWISE: "restore",
        IconName.X_CIRCLE: "close-circle",
        IconName.FILE_TEXT: "file-document",
        IconName.SAVE: "content-save",
        IconName.VOLUME_HIGH: "volume-high",
        IconName.VOLUME_MID: "volume-medium",
        IconName.VOLUME_LOW: "volume-low",
        IconName.VOLUME_OFF: "volume-mute",
        IconName.VOLUME_MUTE: "volume-off",
        IconName.FILE_IMPORT: "file-import",
        IconName.FILE_EXPORT: "file-export",
        IconName.SCENE_ADD: "plus-box-multiple",
        IconName.MARK_IN: "step-backward",
        IconName.MARK_OUT: "step-forward",
        IconName.FRAME_ADD: "plus-box",
        IconName.SIDEBAR_RIGHT: "page-layout-sidebar-right",
        IconName.SIDEBAR_LEFT: "page-layout-sidebar-left",
    }

    @property
    @override
    def id(self) -> str:
        return "material"

    @property
    @override
    def name(self) -> str:
        return "Material Design Icons"

    @property
    @override
    def weights(self) -> frozenset[str]:
        return frozenset({"regular", "outline"})

    @property
    @override
    def default_weight(self) -> str:
        return "regular"

    @override
    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.material")

    @override
    def map_name(self, name: IconName) -> str:
        return self.NAME_MAP.get(name, name.value)


class LucideProvider(IconProvider):
    """Lucide Icons provider."""

    # Mapping from normalized IconName to Lucide icon names
    NAME_MAP: ClassVar[dict[IconName, str]] = {
        IconName.SPINNER_GAP: "loader-circle",
        IconName.UNLINK: "unlink",
        IconName.LINK_2: "link-2",
        IconName.LINK_3: "unlink-2",
        IconName.MAGNIFYING_GLASS: "search-code",
        IconName.FRAME_CORNERS: "scan",
        IconName.ARROWS_OUT_CARDINAL: "move",
        IconName.ARROW_U_TOP_LEFT: "undo-2",
        IconName.ARROW_CLOCKWISE: "rotate-cw",
        IconName.ARROW_COUNTER_CLOCKWISE: "rotate-ccw",
        IconName.X_CIRCLE: "circle-x",
        IconName.FILE_VIDEO: "file-video-camera",
        IconName.SAVE: "save",
        IconName.VOLUME_HIGH: "volume-2",
        IconName.VOLUME_MID: "volume-1",
        IconName.VOLUME_LOW: "volume",
        IconName.VOLUME_OFF: "volume-x",
        IconName.VOLUME_MUTE: "volume-off",
        IconName.PIPETTE: "pipette",
        IconName.FILE_IMPORT: "file-down",
        IconName.FILE_EXPORT: "file-up",
        IconName.SCENE_ADD: "square-plus",
        IconName.MARK_IN: "square-chevron-left",
        IconName.MARK_OUT: "square-chevron-right",
        IconName.FRAME_ADD: "square-arrow-right-enter",
        IconName.SIDEBAR_RIGHT: "panel-right-open",
        IconName.SIDEBAR_LEFT: "panel-left-open",
    }

    @property
    @override
    def id(self) -> str:
        return "lucide"

    @property
    @override
    def name(self) -> str:
        return "Lucide Icons"

    @property
    @override
    def weights(self) -> frozenset[str]:
        return frozenset({"regular"})

    @property
    @override
    def default_weight(self) -> str:
        return "regular"

    @override
    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.lucide")

    @override
    def map_name(self, name: IconName) -> str:
        return self.NAME_MAP.get(name, name.value)


# Provider registry
ICON_PROVIDERS: dict[str, IconProvider] = {
    "phosphor": PhosphorProvider(),
    "material": MaterialProvider(),
    "lucide": LucideProvider(),
}
