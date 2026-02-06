"""Asset utilities for vsview."""

from collections.abc import Callable, Mapping
from contextlib import suppress
from functools import cache, partial
from importlib import resources
from logging import DEBUG, getLogger
from pathlib import Path
from typing import Any, ClassVar
from weakref import WeakKeyDictionary

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton, QWidget
from shiboken6 import Shiboken

from .providers import ICON_PROVIDERS, IconName

_logger = getLogger(__name__)


class IconReloadMixin:
    """
    Mixin for QWidget subclasses with automatic icon hot-reload support.

    Mix this with any QWidget subclass to get automatic icon updates when global settings (icon provider/weight) change.

    Example:
    ```python
    class MyWidget(QWidget, IconReloadMixin):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)

            # Button is automatically registered for icon hot-reload
            self.btn = self.make_tool_button(IconName.LINK, "Link", self)
    ```
    """

    DEFAULT_ICON_STATES: ClassVar[
        Mapping[
            tuple[QIcon.Mode, QIcon.State],
            QPalette.ColorRole | tuple[QPalette.ColorGroup, QPalette.ColorRole],
        ]
    ] = {
        (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ButtonText,
        (QIcon.Mode.Normal, QIcon.State.On): QPalette.ColorRole.Base,
        (QIcon.Mode.Disabled, QIcon.State.Off): (QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
        (QIcon.Mode.Disabled, QIcon.State.On): (QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._button_reloaders = WeakKeyDictionary[QToolButton, Callable[[], None]]()
        self._custom_callbacks = list[Callable[[], None]]()

        from ..app.settings import SettingsManager

        self._settings_manager = SettingsManager()
        self._settings_manager.signals.globalChanged.connect(self._reload_all_icons)

    def deleteLater(self) -> None:
        self._button_reloaders.clear()
        self._custom_callbacks.clear()

        getattr(super(), "deleteLater", lambda: None)()

    def register_icon_button(
        self,
        button: QToolButton,
        icon_name: IconName,
        icon_size: QSize = QSize(20, 20),
        color_role: QPalette.ColorRole = QPalette.ColorRole.ToolTipText,
        icon_states: Mapping[
            tuple[QIcon.Mode, QIcon.State],
            QPalette.ColorRole | tuple[QPalette.ColorGroup, QPalette.ColorRole],
        ]
        | None = None,
    ) -> None:
        """
        Register a button for automatic icon reload when settings change.


        Args:
            button: The QToolButton to update.
            icon_name: The IconName to use for the button.
            icon_size: Size for the icon.
            color_role: Palette color role for simple icons (used when icon_states is None).
            icon_states: Full mapping of (QIcon.Mode, QIcon.State) -> QPalette.ColorRole.
                Allows complete control over icon appearance for all Qt states.
                Example: {
                    (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ToolTipText,
                    (QIcon.Mode.Normal, QIcon.State.On): QPalette.ColorRole.Highlight,
                    (QIcon.Mode.Disabled, QIcon.State.Off): QPalette.ColorRole.Mid,
                }
        """

        def reload(btn: QToolButton) -> None:
            # Qt can delete C++ object while Python wrapper still exists
            if not Shiboken.isValid(btn):
                del btn
                return

            palette = btn.palette()

            if icon_states:
                icon = self.make_icon(
                    {
                        (mode, state): (
                            icon_name,
                            palette.color(*role) if isinstance(role, tuple) else palette.color(role),
                        )
                        for (mode, state), role in icon_states.items()
                    },
                    size=icon_size,
                )
            else:
                icon = self.make_icon((icon_name, palette.color(color_role)), size=icon_size)
            btn.setIcon(icon)

        self._button_reloaders[button] = partial(reload, button)

    def register_icon_callback(self, callback: Callable[[], None]) -> None:
        """
        Register a custom callback for icon reloading.

        Use this for complex icons that don't fit the standard button pattern
        (e.g., play/pause with different icons per state, spinner animations).

        Args:
            callback: A callable that reloads the icon(s).
        """
        self._custom_callbacks.append(callback)

    def _reload_all_icons(self) -> None:
        """Reload all registered icons."""
        for reload in self._button_reloaders.values():
            reload()

        for cb in self._custom_callbacks.copy():
            with suppress(RuntimeError):
                cb()
                continue
            self._custom_callbacks.remove(cb)
            _logger.log(DEBUG - 1, "%r callback failed to reload icon. Removed it.")

    @staticmethod
    def make_icon(
        icons: tuple[IconName, QColor] | dict[tuple[QIcon.Mode, QIcon.State], tuple[IconName, QColor]],
        size: QSize | None = None,
    ) -> QIcon:
        """
        Create a QIcon from either:
        - a single (IconName, color) tuple
        - or a dict mapping (mode, state) -> (IconName, color)

        Args:
            icons: Icon specification using IconName enum.
                Simple usage: (IconName.PLAY, QColor("white"))
                Full control: {
                    (QIcon.Mode.Normal, QIcon.State.Off): (IconName.PLAY, QColor("white")),
                    (QIcon.Mode.Normal, QIcon.State.On): (IconName.PAUSE, QColor("gray")),
                    (QIcon.Mode.Disabled, QIcon.State.Off): (IconName.PLAY, QColor("darkgray")),
                }
            size: Target size for SVG rendering (for crisp output).
        """
        icon = QIcon()
        render_size = size or QSize(256, 256)

        def _load_pixmap(name: IconName, color: QColor) -> QPixmap:
            return load_icon(name, render_size, color)

        # Simple icon
        if isinstance(icons, tuple):
            icon.addPixmap(_load_pixmap(*icons))
            return icon

        # Stateful icon
        for (mode, state), (name, color) in icons.items():
            icon.addPixmap(_load_pixmap(name, color), mode, state)

        return icon

    def make_tool_button(
        self,
        icon: IconName | QIcon,
        tooltip: str,
        parent: QWidget | None = None,
        *,
        checkable: bool = False,
        checked: bool = False,
        register_icon: bool = True,
        icon_size: QSize = QSize(20, 20),
        color: QColor | None = None,
        color_role: QPalette.ColorRole = QPalette.ColorRole.ToolTipText,
        icon_states: Mapping[
            tuple[QIcon.Mode, QIcon.State],
            QPalette.ColorRole | tuple[QPalette.ColorGroup, QPalette.ColorRole],
        ]
        | None = None,
    ) -> QToolButton:
        """
        Create a tool button with an icon and automatically register it for hot-reload when the icon is an IconName.

        Args:
            icon: The icon to display (IconName for auto-creation, or QIcon for pre-made).
            tooltip: Tooltip text for the button.
            parent: Parent widget.
            checkable: Whether the button is checkable.
            checked: Initial checked state (only applies if checkable=True).
            icon_size: Size for the icon.
            color: Explicit color for the icon (overrides color_role).
            color_role: Palette color role for the icon (default: ToolTipText).
                Used when icon_states is None.
            icon_states: Full mapping of (QIcon.Mode, QIcon.State) -> QPalette.ColorRole.
                Allows complete control over icon appearance for all Qt states:
                - Modes: Normal, Disabled, Active, Selected
                - States: Off, On
                Example: {
                    (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ToolTipText,
                    (QIcon.Mode.Normal, QIcon.State.On): QPalette.ColorRole.Mid,
                    (QIcon.Mode.Disabled, QIcon.State.Off): QPalette.ColorRole.Dark,
                }

        Returns:
            A configured QToolButton instance.
        """
        btn = QToolButton(parent)
        btn.setCheckable(checkable)
        btn.setToolTip(tooltip)

        if checkable:
            btn.setChecked(checked)

        if isinstance(icon, QIcon):
            btn.setIcon(icon)
        elif isinstance(icon, IconName):
            palette = btn.palette()

            if icon_states is not None:
                state_icons = {}
                for (mode, state), role in icon_states.items():
                    c = palette.color(*role) if isinstance(role, tuple) else palette.color(role)
                    state_icons[(mode, state)] = (icon, c)

                q_icon = self.make_icon(state_icons, size=icon_size)
            else:
                # Simple single-color icon
                btn_color = color if color is not None else palette.color(color_role)
                q_icon = self.make_icon((icon, btn_color), size=icon_size)

            btn.setIcon(q_icon)

            if register_icon:
                self.register_icon_button(btn, icon, icon_size, color_role, icon_states)

        btn.setIconSize(icon_size)
        btn.setAutoRaise(True)
        return btn


def load_svg(svg_data: bytes, size: QSize, color: QColor | None = None) -> QPixmap:
    renderer = QSvgRenderer(svg_data)
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)

    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        renderer.render(painter)

        if color is not None:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QColor(color))

    return pixmap


def load_icon(
    name: IconName,
    size: QSize | int,
    color: QColor | Qt.GlobalColor | None = None,
    *,
    provider: str | None = None,
    weight: str | None = None,
) -> QPixmap:
    # Get from settings if not specified
    if provider is None or weight is None:
        from ..app.settings import SettingsManager

        provider = provider or SettingsManager.global_settings.appearance.icon_provider
        weight = weight or SettingsManager.global_settings.appearance.icon_weight

    if isinstance(size, int):
        size = QSize(size, size)

    svg_data = ICON_PROVIDERS[provider].get_icon_path(name, weight).read_bytes()

    return load_svg(svg_data, size, QColor(color) if color is not None else None)


@cache
def load_fonts() -> None:
    """Load bundled fonts into Qt's font database."""
    fonts = resources.files("vsview.assets.fonts")

    for font_file in [
        "Cascadia_Mono/CascadiaMono-VariableFont_wght.ttf",
        "Cascadia_Mono/CascadiaMono-Italic-VariableFont_wght.ttf",
    ]:
        try:
            font_data = fonts.joinpath(font_file).read_bytes()
            font_id = QFontDatabase.addApplicationFontFromData(font_data)

            if font_id < 0:
                _logger.warning("Failed to load font: %s", font_file)
            else:
                _logger.debug("Loaded font %s: %r", font_file, lambda: QFontDatabase.applicationFontFamilies(font_id))
        except Exception as e:
            _logger.warning("Error loading font %s: %s", font_file, e)


@cache
def get_monospace_font(size: int | None = None) -> QFont:
    """
    Get the preferred monospace font.

    Returns Cascadia Mono if available (after load_fonts() is called), otherwise falls back to system monospace font.

    Args:
        size: Optional point size for the font.

    Returns:
        A QFont configured for monospace display.
    """
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setStyleStrategy(QFont.StyleStrategy.PreferQuality | QFont.StyleStrategy.PreferAntialias)
    font.setWeight(QFont.Weight.DemiBold)

    if size is not None:
        font.setPointSize(size)

    return font


@cache
def app_icon() -> QIcon:
    return QIcon(str(Path(__file__).parent / "icon.png"))


@cache
def loading_icon() -> QIcon:
    return QIcon(str(Path(__file__).parent / "loading.png"))
