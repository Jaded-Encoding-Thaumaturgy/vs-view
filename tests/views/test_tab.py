import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from vsview.app.views.plugins import PluginSplitter
from vsview.app.views.tab import BaseTabWidget, SimpleTabLabel, TabLabel, TabViewWidget
from vsview.app.views.video import GraphicsView

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def test_base_tab_widget_selection_lifecycle(qtbot: QtBot) -> None:
    """Verify BaseTabWidget automatically synchronizes label states on tab change."""
    parent = QWidget()
    qtbot.addWidget(parent)

    tabs = BaseTabWidget(parent)
    qtbot.addWidget(tabs)

    lbl0 = SimpleTabLabel("Tab 0", tabs)
    lbl1 = SimpleTabLabel("Tab 1", tabs)
    qtbot.addWidget(lbl0)
    qtbot.addWidget(lbl1)

    idx0 = tabs.addTab(QWidget(parent), "")
    tabs.set_tab_label(idx0, lbl0)

    idx1 = tabs.addTab(QWidget(parent), "")
    tabs.set_tab_label(idx1, lbl1)

    # Unlabeled tab to verify graceful fallback
    tabs.addTab(QWidget(parent), "Unlabeled")

    # Initial state
    assert tabs.get_tab_label(idx0) is lbl0
    assert tabs.get_tab_label(idx1) is lbl1
    assert tabs.get_tab_label(2) is None
    assert lbl0.is_selected is True
    assert lbl1.is_selected is False

    # Switch to tab 1
    with qtbot.waitSignal(tabs.currentChanged, timeout=1000):
        tabs.setCurrentIndex(1)
    assert lbl0.is_selected is False
    assert lbl1.is_selected is True

    # Switch to unlabeled tab 2: custom labels deactivate without error
    with qtbot.waitSignal(tabs.currentChanged, timeout=1000):
        tabs.setCurrentIndex(2)
    assert lbl0.is_selected is False
    assert lbl1.is_selected is False


def test_tab_view_widget_contract_and_history(qtbot: QtBot) -> None:
    """Verify TabViewWidget maintains previous_tab_index and get_tab_label contracts."""
    parent = QWidget()
    qtbot.addWidget(parent)

    tabs = TabViewWidget(parent)
    qtbot.addWidget(tabs)

    v0 = GraphicsView(parent)
    v1 = GraphicsView(parent)
    qtbot.addWidget(v0)
    qtbot.addWidget(v1)

    lbl0 = TabLabel("Clip 0", 0, tabs)
    lbl1 = TabLabel("Clip 1", 1, tabs)
    qtbot.addWidget(lbl0)
    qtbot.addWidget(lbl1)

    tabs.set_tab_label(tabs.addTab(v0, ""), lbl0)
    tabs.set_tab_label(tabs.addTab(v1, ""), lbl1)

    # Contract check: get_tab_label raises ValueError on non-existent tab
    with pytest.raises(ValueError, match="Tab label not found"):
        tabs.get_tab_label(99)

    # Navigation updates history and selection in sync
    with qtbot.waitSignal(tabs.currentChanged, timeout=1000):
        tabs.setCurrentIndex(1)
    assert tabs.previous_tab_index == 0
    assert lbl0.is_selected is False
    assert lbl1.is_selected is True

    with qtbot.waitSignal(tabs.currentChanged, timeout=1000):
        tabs.setCurrentIndex(0)
    assert tabs.previous_tab_index == 1
    assert lbl0.is_selected is True
    assert lbl1.is_selected is False


def test_plugin_splitter_tab_selection(qtbot: QtBot) -> None:
    """Verify PluginSplitter tabs sync selection state on plugin switching."""
    splitter = PluginSplitter()
    qtbot.addWidget(splitter)
    splitter.insert_main_widget(QWidget())

    p0 = QWidget()
    p1 = QWidget()
    qtbot.addWidget(p0)
    qtbot.addWidget(p1)

    splitter.add_plugin(p0, "Plugin 0")
    splitter.add_plugin(p1, "Plugin 1")

    lbl0 = splitter.plugin_tabs.get_tab_label(0)
    lbl1 = splitter.plugin_tabs.get_tab_label(1)
    assert isinstance(lbl0, SimpleTabLabel)
    assert isinstance(lbl1, SimpleTabLabel)
    assert lbl0.is_selected is True
    assert lbl1.is_selected is False

    with qtbot.waitSignal(splitter.plugin_tabs.currentChanged, timeout=1000):
        splitter.plugin_tabs.setCurrentIndex(1)
    assert lbl0.is_selected is False
    assert lbl1.is_selected is True
