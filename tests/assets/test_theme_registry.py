from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from vsview.assets.themes import ColorScheme, ThemeRegistry

pytestmark = [pytest.mark.qt]


def test_theme_get_and_properties() -> None:
    theme = ThemeRegistry.get("dracula")
    assert theme is not None
    assert theme.id == "dracula"
    assert theme.name == "Dracula"
    assert theme.scheme == ColorScheme.DARK
    assert theme.is_dark is True
    assert theme.is_custom is False

    assert ThemeRegistry.get("non_existent_theme_id") is None


def test_theme_get_for_scheme(qapp: QApplication) -> None:
    light_themes = ThemeRegistry.get_for_scheme(Qt.ColorScheme.Light)
    assert len(light_themes) == 12
    assert {t.id for t in light_themes} == {
        "adwaita_light",
        "ayu_light",
        "breeze_light",
        "catppuccin_latte",
        "everforest_light",
        "github_light",
        "gruvbox_light",
        "kanagawa_lotus",
        "modern_light",
        "rose_pine_dawn",
        "solarized_light",
        "tokyo_night_day",
    }
    assert all(t.scheme == ColorScheme.LIGHT for t in light_themes)

    dark_themes = ThemeRegistry.get_for_scheme(Qt.ColorScheme.Dark)
    assert len(dark_themes) == 24
    assert all(t.scheme == ColorScheme.DARK for t in dark_themes)


def test_theme_create_palette(qapp: QApplication) -> None:
    theme = ThemeRegistry.get("dracula")
    assert theme is not None

    palette = theme.create_palette()
    assert isinstance(palette, QPalette)

    # In dark themes, Base is mantle and AlternateBase is base
    assert palette.color(QPalette.ColorRole.Base) == theme.palette.mantle
    assert palette.color(QPalette.ColorRole.AlternateBase) == theme.palette.base
    assert palette.color(QPalette.ColorRole.Window) == theme.palette.base
    assert palette.color(QPalette.ColorRole.Highlight) == theme.palette.primary
    assert palette.color(QPalette.ColorRole.Accent) == theme.palette.secondary


def test_theme_create_palette_light(qapp: QApplication) -> None:
    theme = ThemeRegistry.get("github_light")
    assert theme is not None

    palette = theme.create_palette()
    assert isinstance(palette, QPalette)

    # In light themes, Base is crust and AlternateBase is mantle
    assert palette.color(QPalette.ColorRole.Base) == theme.palette.crust
    assert palette.color(QPalette.ColorRole.AlternateBase) == theme.palette.mantle
    assert palette.color(QPalette.ColorRole.Window) == theme.palette.base
