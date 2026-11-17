from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QTreeWidget
from pytestqt.qtbot import QtBot

from vsview.app.settings.models import AppearanceSettings
from vsview.app.themes import AppearanceManager
from vsview.assets.themes import Theme, ThemeRegistry

pytestmark = [pytest.mark.qt]


def test_appearance_manager_apply_style(qapp: QApplication) -> None:
    AppearanceManager.apply_style("Fusion")
    assert AppearanceManager.active_style == "fusion"


@pytest.mark.parametrize(
    ("theme_id", "scheme"), [("dracula", Qt.ColorScheme.Dark), ("github_light", Qt.ColorScheme.Light)]
)
def test_apply_palette_applies_a_theme_of_the_matching_scheme(
    qapp: QApplication, theme_id: str, scheme: Qt.ColorScheme
) -> None:
    AppearanceManager.apply_palette(None, scheme)
    received_theme = list[Theme | None]()
    AppearanceManager.signals.themeChanged.connect(received_theme.append)

    changed = AppearanceManager.apply_palette(theme_id, scheme)

    theme = AppearanceManager.active_theme
    assert changed is True
    assert theme is not None
    assert theme.id == theme_id
    assert AppearanceManager.active_color_scheme == scheme
    assert received_theme == [theme]
    assert qapp.palette().color(QPalette.ColorRole.WindowText) == theme.palette.text


@pytest.mark.parametrize(
    ("theme_id", "scheme"),
    [
        ("github_light", Qt.ColorScheme.Dark),
        ("dracula", Qt.ColorScheme.Light),
        ("unknown_theme_xyz", Qt.ColorScheme.Dark),
    ],
    ids=["light_theme_in_dark_scheme", "dark_theme_in_light_scheme", "unknown_theme"],
)
def test_apply_palette_rejects_a_theme_it_cannot_apply(
    qapp: QApplication, theme_id: str, scheme: Qt.ColorScheme
) -> None:
    AppearanceManager.apply_palette(None, scheme)

    with pytest.raises(ValueError, match=theme_id):
        AppearanceManager.apply_palette(theme_id, scheme)

    assert AppearanceManager.active_theme is None


def test_apply_palette_without_a_theme_clears_the_active_theme(qapp: QApplication) -> None:
    AppearanceManager.apply_palette("dracula", Qt.ColorScheme.Dark)
    received_theme = list[Theme | None]()
    AppearanceManager.signals.themeChanged.connect(received_theme.append)

    changed = AppearanceManager.apply_palette(None, Qt.ColorScheme.Dark)

    assert changed is True
    assert AppearanceManager.active_theme is None
    assert received_theme == [None]
    assert AppearanceManager.active_color_scheme == Qt.ColorScheme.Dark


def test_appearance_manager_apply_stylesheet(qapp: QApplication, tmp_path: Path) -> None:
    qss_file = tmp_path / "custom.qss"
    qss_file.write_text("QWidget { color: red; }", encoding="utf-8")

    # Apply valid stylesheet
    assert AppearanceManager.apply_stylesheet(str(qss_file)) is True
    assert qapp.styleSheet() == "QWidget { color: red; }"

    # Same content should return False (no change)
    assert AppearanceManager.apply_stylesheet(str(qss_file)) is False

    # Clear stylesheet
    assert AppearanceManager.apply_stylesheet(None) is True
    assert qapp.styleSheet() == ""


def test_appearance_manager_apply_all(qapp: QApplication) -> None:
    appearance_signals = 0

    def on_appearance_changed() -> None:
        nonlocal appearance_signals
        appearance_signals += 1

    AppearanceManager.signals.appearanceChanged.connect(on_appearance_changed)

    settings = AppearanceSettings(style="Fusion", theme=Qt.ColorScheme.Dark, palette_theme="dracula")

    AppearanceManager.apply_all(settings)
    assert AppearanceManager.active_theme is not None
    assert AppearanceManager.active_theme.id == "dracula"
    assert AppearanceManager.active_style == "fusion"
    assert appearance_signals == 1


def test_appearance_manager_repolishes_local_stylesheet_widgets_on_palette_change(
    qapp: QApplication, qtbot: QtBot
) -> None:
    AppearanceManager.apply_all(AppearanceSettings(style="Fusion", theme=Qt.ColorScheme.Dark, palette_theme="dracula"))

    tree = QTreeWidget()
    qtbot.addWidget(tree)
    tree.setStyleSheet("QTreeWidget { border: none; } QTreeWidget::item { padding: 4px 2px; }")
    tree.show()

    dracula = ThemeRegistry.get("dracula")
    assert dracula is not None
    assert tree.viewport().palette().color(QPalette.ColorRole.Base) == dracula.palette.mantle

    AppearanceManager.apply_all(AppearanceSettings(style="Fusion", theme=Qt.ColorScheme.Dark, palette_theme="nord"))

    nord = ThemeRegistry.get("nord")
    assert nord is not None
    assert tree.palette().color(QPalette.ColorRole.Base) == nord.palette.mantle
    assert tree.viewport().palette().color(QPalette.ColorRole.Base) == nord.palette.mantle


def test_appearance_manager_switch_from_custom_to_stock_palette(qapp: QApplication) -> None:
    # Apply Fusion + custom dark theme
    AppearanceManager.apply_all(AppearanceSettings(style="Fusion", theme=Qt.ColorScheme.Dark, palette_theme="dracula"))
    assert AppearanceManager.active_theme is not None
    assert AppearanceManager.active_theme.id == "dracula"

    # Switch to windows11 with stock palette (None) in Dark mode
    AppearanceManager.apply_all(AppearanceSettings(style="windows11", theme=Qt.ColorScheme.Dark, palette_theme=None))

    assert AppearanceManager.active_theme is None

    # On platforms supporting OS color schemes (e.g. native Windows platform theme),
    # verify that the stock palette honors dark mode rather than classic Windows 95 light (#d4d0c8).
    if qapp.styleHints().colorScheme() == Qt.ColorScheme.Dark:
        window_color = qapp.palette().color(QPalette.ColorRole.Window)
        base_color = qapp.palette().color(QPalette.ColorRole.Base)
        assert window_color.valueF() < 0.5, f"Expected dark Window color, got {window_color.name()}"
        assert base_color.valueF() < 0.5, f"Expected dark Base color, got {base_color.name()}"
