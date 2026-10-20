"""Unit tests for cascading dropdown dependencies in settings."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox
from pytestqt.qtbot import QtBot

from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.dialog import SettingsDialog
from vsview.app.settings.metadata import Dropdown

pytestmark = [pytest.mark.unit]


@pytest.mark.qt
def test_dropdown_populate_static_items(qtbot: QtBot) -> None:
    dd = Dropdown(label="Test", items=[("Option 1", 1), ("Option 2", 2)])
    combo = QComboBox()
    qtbot.addWidget(combo)
    dd.populate(combo)

    assert combo.count() == 2
    assert combo.itemText(0) == "Option 1"
    assert combo.itemData(0) == 1
    assert combo.itemText(1) == "Option 2"
    assert combo.itemData(1) == 2


@pytest.mark.qt
def test_dropdown_populate_dynamic_callable(qtbot: QtBot) -> None:
    def dynamic_items(parent_val: int | None = None) -> list[tuple[str, int]]:
        multiplier = parent_val or 1
        return [("One", 1 * multiplier), ("Two", 2 * multiplier)]

    dd = Dropdown(label="Dynamic", depends_on="parent.val", items=dynamic_items)
    combo = QComboBox()
    qtbot.addWidget(combo)

    dd.populate(combo, 10)
    assert combo.count() == 2
    assert combo.itemData(0) == 10
    assert combo.itemData(1) == 20

    dd.populate(combo, 5)
    assert combo.count() == 2
    assert combo.itemData(0) == 5
    assert combo.itemData(1) == 10


@pytest.mark.qt
def test_dropdown_selection_retention_and_invalidation(qtbot: QtBot) -> None:
    def items_for_category(category: str | None = None) -> list[tuple[str, str]]:
        if category == "fruits":
            return [("Apple", "apple"), ("Banana", "banana")]
        if category == "vegetables":
            return [("Carrot", "carrot"), ("Banana", "banana")]
        return []

    dd = Dropdown(label="Produce", depends_on="category", items=items_for_category, default_value="default_produce")
    combo = QComboBox()
    qtbot.addWidget(combo)

    dd.populate(combo, "fruits")
    combo.setCurrentIndex(combo.findData("banana"))
    assert combo.currentData() == "banana"

    dd.populate(combo, "vegetables")
    assert combo.currentIndex() >= 0
    assert combo.currentData() == "carrot"

    combo.setCurrentIndex(combo.findData("banana"))
    assert combo.currentData() == "banana"

    dd.populate(combo, "fruits")
    assert dd.get_value(combo) == "apple"


@pytest.mark.qt
def test_settings_dialog_cascading_dropdowns(init_plugin_manager: PluginManager, qtbot: QtBot) -> None:
    dialog = SettingsDialog(None)
    qtbot.addWidget(dialog)

    theme_combo = dialog._global_widgets.get("appearance.theme")
    palette_combo = dialog._global_widgets.get("appearance.palette_theme")
    provider_combo = dialog._global_widgets.get("appearance.icon_provider")
    weight_combo = dialog._global_widgets.get("appearance.icon_weight")

    assert isinstance(theme_combo, QComboBox)
    assert isinstance(palette_combo, QComboBox)
    assert isinstance(provider_combo, QComboBox)
    assert isinstance(weight_combo, QComboBox)

    # Verify initial population
    assert palette_combo.count() > 0
    assert weight_combo.count() > 0

    # Test Icon Provider cascading change
    # Set to Lucide (which only has 1 weight: regular)
    lucide_idx = provider_combo.findData("lucide")
    assert lucide_idx >= 0
    provider_combo.setCurrentIndex(lucide_idx)

    assert weight_combo.count() == 1
    assert weight_combo.itemData(0) == "regular"

    # Set to Phosphor (which has multiple weights: regular, bold, etc.)
    phosphor_idx = provider_combo.findData("phosphor")
    assert phosphor_idx >= 0
    provider_combo.setCurrentIndex(phosphor_idx)

    assert weight_combo.count() > 1
    assert any(weight_combo.itemData(i) == "bold" for i in range(weight_combo.count()))

    # Test Theme cascading change: switch to Light
    light_idx = theme_combo.findData(Qt.ColorScheme.Light)
    assert light_idx >= 0
    theme_combo.setCurrentIndex(light_idx)
