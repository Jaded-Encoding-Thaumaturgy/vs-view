from collections.abc import Iterator, Set  # noqa: PYI025
from importlib import resources
from importlib.resources.abc import Traversable
from logging import getLogger
from pathlib import Path

from jetpytools import Singleton, inject_self
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .models import ColorScheme, Theme

logger = getLogger(__name__)


class ThemeRegistry(Singleton):
    def __init__(self) -> None:
        self._themes: Set[Theme] | None = None

    @inject_self
    def reload(self) -> None:
        self._themes = None
        self._ensure_loaded()

    @inject_self
    def get(self, theme_id: str) -> Theme | None:
        """Return theme by ID if found, otherwise None."""
        themes = self._ensure_loaded()

        if (theme := next((t for t in themes if t.id == theme_id), None)) is not None:
            return theme

        logger.warning("Cannot find theme %r.", theme_id)
        return None

    @inject_self
    def get_for_scheme(self, color_scheme: Qt.ColorScheme | None = None) -> list[Theme]:
        """
        Return themes matching the target color scheme.

        If color_scheme is None, resolves the target from the application style hints.
        """
        if color_scheme is None or color_scheme == Qt.ColorScheme.Unknown:
            app = QApplication.instance()
            target_scheme = app.styleHints().colorScheme() if isinstance(app, QApplication) else Qt.ColorScheme.Dark
        else:
            target_scheme = color_scheme

        expected_scheme = ColorScheme.DARK if target_scheme != Qt.ColorScheme.Light else ColorScheme.LIGHT
        themes = self._ensure_loaded()

        return [theme for theme in sorted(themes, key=lambda t: t.name) if theme.scheme == expected_scheme]

    @property
    def _user_themes_dir(self) -> Path:
        from ...app.settings.models import GlobalSettings

        return GlobalSettings.config_path / "themes"

    def _ensure_loaded(self) -> Set[Theme]:
        if self._themes is not None:
            return self._themes

        themes = list[Theme]()

        for resource in self._bundled_files():
            try:
                theme = Theme.model_validate_json(resource.read_bytes())
            except ValidationError as e:
                logger.warning("Invalid bundled theme %r: %s", resource.name, e)
            else:
                themes.append(theme)

        if self._user_themes_dir.is_dir():
            for path in self._user_themes_dir.glob("*.json"):
                try:
                    theme = Theme.model_validate_json(path.read_bytes())
                    theme_with_custom = theme.model_copy(update={"is_custom": True})
                except ValidationError as e:
                    logger.warning("Invalid user theme %r: %s", path.name, e)
                else:
                    themes.append(theme_with_custom)

        self._themes = dict.fromkeys(themes).keys()
        return self._themes

    @staticmethod
    def _bundled_files() -> Iterator[Traversable]:
        for subdir in resources.files("vsview.assets.themes.builtin").iterdir():
            if not subdir.is_dir():
                continue
            for entry in subdir.iterdir():
                if entry.is_file() and entry.name.endswith(".json"):
                    yield entry
