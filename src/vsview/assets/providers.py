"""
Icon provider abstraction for vsview.

Provides a unified interface for loading icons from different providers with normalized icon names.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from importlib import resources
from importlib.resources.abc import Traversable
from typing import ClassVar


class IconName(StrEnum):
    PLAY = "play"
    PAUSE = "pause"
    FAST_FORWARD = "fast-forward"
    REWIND = "rewind"
    SKIP_FORWARD = "skip-forward"
    SKIP_BACK = "skip-back"
    CHECK = "check"
    SPINNER_GAP = "spinner-gap"
    LINK = "link"
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
    # Audio icons
    VOLUME_HIGH = "speaker-high"
    VOLUME_MID = "speaker-low"
    VOLUME_LOW = "speaker-none"
    VOLUME_OFF = "speaker-x"
    VOLUME_MUTE = "speaker-slash"
    #
    PIPETTE = "eyedropper"
    CLIPBOARD = "clipboard"


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


class PhosphorProvider(IconProvider):
    """Phosphor Icons provider."""

    @property
    def id(self) -> str:
        return "phosphor"

    @property
    def name(self) -> str:
        return "Phosphor Icons"

    @property
    def weights(self) -> frozenset[str]:
        return frozenset({"thin", "light", "regular", "bold", "fill", "duotone"})

    @property
    def default_weight(self) -> str:
        return "regular"

    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.phosphor")

    def map_name(self, name: IconName) -> str:
        # Phosphor uses same kebab-case names as IconName
        return name.value


class MaterialProvider(IconProvider):
    """Material Design Icons provider."""

    # Mapping from normalized IconName to Material icon names
    NAME_MAP: ClassVar[dict[IconName, str]] = {
        IconName.SKIP_FORWARD: "skip-next",
        IconName.SKIP_BACK: "skip-previous",
        IconName.SPINNER_GAP: "loading",
        IconName.LINK: "link-variant",
        IconName.MAGNIFYING_GLASS: "magnify",
        IconName.FRAME_CORNERS: "border-radius",
        IconName.ARROWS_OUT_CARDINAL: "arrow-expand-all",
        IconName.ARROW_U_TOP_LEFT: "arrow-u-left-top",
        IconName.X_CIRCLE: "close-circle",
        IconName.FILE_TEXT: "file-document",
        IconName.SAVE: "content-save",
        IconName.VOLUME_HIGH: "volume-high",
        IconName.VOLUME_MID: "volume-medium",
        IconName.VOLUME_LOW: "volume-low",
        IconName.VOLUME_OFF: "volume-mute",
        IconName.VOLUME_MUTE: "volume-off",
    }

    @property
    def id(self) -> str:
        return "material"

    @property
    def name(self) -> str:
        return "Material Design Icons"

    @property
    def weights(self) -> frozenset[str]:
        return frozenset({"regular", "outline"})

    @property
    def default_weight(self) -> str:
        return "regular"

    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.material")

    def map_name(self, name: IconName) -> str:
        return self.NAME_MAP.get(name, name.value)


class LucideProvider(IconProvider):
    """Lucide Icons provider."""

    # Mapping from normalized IconName to Lucide icon names
    NAME_MAP: ClassVar[dict[IconName, str]] = {
        IconName.SPINNER_GAP: "loader-circle",
        IconName.MAGNIFYING_GLASS: "search-code",
        IconName.FRAME_CORNERS: "scan",
        IconName.ARROWS_OUT_CARDINAL: "move",
        IconName.ARROW_U_TOP_LEFT: "undo-2",
        IconName.X_CIRCLE: "circle-x",
        IconName.FILE_VIDEO: "file-video-camera",
        IconName.SAVE: "save",
        IconName.VOLUME_HIGH: "volume-2",
        IconName.VOLUME_MID: "volume-1",
        IconName.VOLUME_LOW: "volume",
        IconName.VOLUME_OFF: "volume-x",
        IconName.VOLUME_MUTE: "volume-off",
        IconName.PIPETTE: "pipette",
    }

    @property
    def id(self) -> str:
        return "lucide"

    @property
    def name(self) -> str:
        return "Lucide Icons"

    @property
    def weights(self) -> frozenset[str]:
        return frozenset({"regular"})

    @property
    def default_weight(self) -> str:
        return "regular"

    def get_folder(self) -> Traversable:
        return resources.files("vsview.assets.icons.lucide")

    def map_name(self, name: IconName) -> str:
        return self.NAME_MAP.get(name, name.value)


# Provider registry
ICON_PROVIDERS: dict[str, IconProvider] = {
    "phosphor": PhosphorProvider(),
    "material": MaterialProvider(),
    "lucide": LucideProvider(),
}
