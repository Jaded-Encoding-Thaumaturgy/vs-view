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


def test_appearance_manager_init(qapp: QApplication) -> None:
    manager = AppearanceManager()
    assert manager is AppearanceManager()
    assert bool(manager.active_style)
    assert manager.active_style == AppearanceManager.active_style


def test_appearance_manager_apply_style(qapp: QApplication) -> None:
    AppearanceManager.apply_style("Fusion")
    assert AppearanceManager.active_style == "fusion"


def test_appearance_manager_apply_palette(qapp: QApplication) -> None:
    manager = AppearanceManager()
    received_theme = list[Theme | None]()
    manager.signals.theme_changed.connect(received_theme.append)

    # Apply dark palette theme with Dark color scheme
    changed = AppearanceManager.apply_palette("dracula", Qt.ColorScheme.Dark)
    assert changed is True
    assert AppearanceManager.active_theme is not None
    assert AppearanceManager.active_theme.id == "dracula"
    assert AppearanceManager.active_color_scheme == Qt.ColorScheme.Dark
    assert len(received_theme) == 1
    assert received_theme[-1] is not None
    assert received_theme[-1].id == "dracula"

    assert qapp.palette().color(qapp.palette().ColorRole.Base) == AppearanceManager.active_theme.palette.mantle

    # Reject mismatched theme: Light theme with Dark color scheme
    with pytest.raises(ValueError, match="Cannot apply light theme 'github_light' when active color scheme is dark"):
        AppearanceManager.apply_palette("github_light", Qt.ColorScheme.Dark)

    # Apply light palette theme with Light color scheme
    changed = AppearanceManager.apply_palette("github_light", Qt.ColorScheme.Light)
    assert changed is True
    assert AppearanceManager.active_theme is not None
    assert AppearanceManager.active_theme.id == "github_light"
    assert AppearanceManager.active_color_scheme == Qt.ColorScheme.Light
    assert received_theme[-1] is not None
    assert received_theme[-1].id == "github_light"

    # Reject mismatched theme: Dark theme with Light color scheme
    with pytest.raises(ValueError, match="Cannot apply dark theme 'dracula' when active color scheme is light"):
        AppearanceManager.apply_palette("dracula", Qt.ColorScheme.Light)

    # Reject unknown palette theme
    with pytest.raises(ValueError, match="Unknown palette theme"):
        AppearanceManager.apply_palette("unknown_theme_xyz", Qt.ColorScheme.Dark)

    # Clear palette theme - revert to style standardPalette and OS scheme
    changed = AppearanceManager.apply_palette(None, Qt.ColorScheme.Dark)
    assert changed is True
    assert AppearanceManager.active_theme is None
    assert received_theme[-1] is None
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

    AppearanceManager.signals.appearance_changed.connect(on_appearance_changed)

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
