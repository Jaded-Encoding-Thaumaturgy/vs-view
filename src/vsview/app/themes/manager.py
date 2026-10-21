from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jetpytools import CustomRuntimeError, CustomValueError, Singleton, inject_self
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from ...assets.themes import ColorScheme, Theme, ThemeRegistry
from ..utils import WeakableSignals

if TYPE_CHECKING:
    from ..settings.models import AppearanceSettings

__all__ = ["AppearanceManager", "AppearanceSignals"]

logger = getLogger(__name__)


class AppearanceSignals(WeakableSignals):
    appearanceChanged = Signal()
    themeChanged = Signal(object)  # Emits Theme | None

    def connect_appearance_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.appearanceChanged, slot, type)

    def connect_theme_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.themeChanged, slot, type)


class AppearanceManager(Singleton):
    def __init__(self) -> None:
        self._signals = AppearanceSignals(QApplication.instance())
        self._active_theme: Theme | None = None
        self._active_style_name: str = ""
        self._active_stylesheet_content: str = ""
        self._active_color_scheme: Qt.ColorScheme | None = None

    @inject_self.property
    def signals(self) -> AppearanceSignals:
        return self._signals

    @inject_self.property
    def active_theme(self) -> Theme | None:
        """Return the currently applied palette theme, or None if using default style palette."""
        return self._active_theme

    @inject_self.property
    def active_color_scheme(self) -> Qt.ColorScheme | None:
        """Return the currently requested OS color scheme."""
        return self._active_color_scheme

    @inject_self.property
    def active_style(self) -> str:
        """Return the active widget style name."""
        return self._active_style_name or self._app.style().name().lower()

    @inject_self
    def apply_all(self, appearance: AppearanceSettings) -> bool:
        """
        Apply widget style, palette theme, OS color scheme, and stylesheet from appearance settings.

        Returns True if any appearance component was updated.
        """
        style_changed = self.apply_style(appearance.style)
        palette_changed = self.apply_palette(appearance.palette_theme, appearance.theme)
        stylesheet_changed = self.apply_stylesheet(appearance.custom_stylesheet)

        any_changed = style_changed or palette_changed or stylesheet_changed

        if any_changed:
            self._repolish_all()
            self._signals.appearanceChanged.emit()

        return any_changed

    @inject_self
    def apply_style(self, style_name: str | None) -> bool:
        """Apply a Qt widget style (e.g. 'Fusion')."""
        current_style = self._app.style().name().lower()
        target_style = (style_name or "").lower()

        if target_style and target_style != current_style:
            self._app.setStyle(style_name or "")
            self._active_style_name = self._app.style().name().lower()
            return True

        if not current_style and target_style:
            self._app.setStyle(style_name or "")
            self._active_style_name = self._app.style().name().lower()
            return True

        return False

    @inject_self
    def apply_color_scheme(self, color_scheme: Qt.ColorScheme | None) -> bool:
        """Apply OS color scheme hint (Dark, Light, or System Default)."""
        if self._active_color_scheme != color_scheme:
            self._active_color_scheme = color_scheme
            if color_scheme is None:
                self._app.styleHints().unsetColorScheme()
            else:
                self._app.styleHints().setColorScheme(color_scheme)
            return True
        return False

    @inject_self
    def apply_palette(self, palette_theme_id: str | None, color_scheme: Qt.ColorScheme | None) -> bool:
        """
        Apply palette theme conforming to the requested color scheme.
        Raises ValueError if the theme does not match the active color scheme.
        """
        color_scheme_changed = self.apply_color_scheme(color_scheme)
        palette_changed = False

        if not palette_theme_id:
            if self._active_theme is not None:
                self._active_theme = None
                self._app.setPalette(QPalette())
                self._signals.themeChanged.emit(None)
                palette_changed = True
            elif color_scheme_changed:
                self._app.setPalette(QPalette())
            return color_scheme_changed or palette_changed

        theme = ThemeRegistry.get(palette_theme_id)
        if not theme:
            raise ValueError(f"Unknown palette theme: {palette_theme_id!r}")

        # Resolve expected scheme (Dark, Light, or resolve System Default)
        active_scheme = color_scheme
        if active_scheme is None or active_scheme == Qt.ColorScheme.Unknown:
            active_scheme = self._app.styleHints().colorScheme()

        expected_scheme = ColorScheme.DARK if active_scheme != Qt.ColorScheme.Light else ColorScheme.LIGHT

        if theme.scheme != expected_scheme:
            raise CustomValueError(
                f"Cannot apply {theme.scheme} theme '{theme.id}' when active color scheme is {expected_scheme}.",
                self.apply_palette,
            )

        if self._active_theme != theme:
            self._app.setPalette(theme.create_palette())
            self._active_theme = theme
            self._signals.themeChanged.emit(theme)
            palette_changed = True

        return color_scheme_changed or palette_changed

    @inject_self
    def apply_stylesheet(self, stylesheet_path: str | None) -> bool:
        """Apply a custom stylesheet from file path, or clear it if None/empty."""
        content = self._read_stylesheet(stylesheet_path)
        if content != self._active_stylesheet_content or content != self._app.styleSheet():
            self._app.setStyleSheet(content)
            self._active_stylesheet_content = content
            return True
        return False

    @property
    def _app(self) -> QApplication:
        if isinstance(app := QApplication.instance(), QApplication):
            return app
        raise CustomRuntimeError("AppearanceManager requires an active QApplication instance.")

    def _read_stylesheet(self, stylesheet_path: str | None) -> str:
        if not stylesheet_path:
            return ""

        path = Path(stylesheet_path)
        if not path.is_file():
            logger.warning("Custom stylesheet %r is not a valid file.", stylesheet_path)
            return ""

        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read custom stylesheet %r: %s", stylesheet_path, e)
            return ""

    def _repolish_all(self) -> None:
        for widget in self._app.allWidgets():
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.ensurePolished()
            widget.update()
