from __future__ import annotations

from collections.abc import Callable
from itertools import count

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget
from pytestqt.qtbot import QtBot

from vsview.app.settings.shortcuts import ShortcutManager

pytestmark = [pytest.mark.unit, pytest.mark.qt]

_ids = count()


def unique_id(name: str) -> str:
    return f"test_scopes.{name}_{next(_ids)}"


@pytest.fixture
def make_widget(qtbot: QtBot) -> Callable[[QWidget | None], QWidget]:
    def factory(parent: QWidget | None = None) -> QWidget:
        widget = QWidget(parent)
        if parent is None:
            qtbot.addWidget(widget)
        return widget

    return factory


@pytest.fixture
def tree(make_widget: Callable[[QWidget | None], QWidget]) -> dict[str, QWidget]:
    """
    window
    ├── workspace
    │   ├── plugin
    │   │   └── plugin_child
    │   └── sibling_plugin
    other_window
    """
    window = make_widget(None)
    workspace = make_widget(window)
    plugin = make_widget(workspace)
    return {
        "window": window,
        "workspace": workspace,
        "plugin": plugin,
        "plugin_child": make_widget(plugin),
        "sibling_plugin": make_widget(workspace),
        "other_window": make_widget(None),
    }


WIDGET = Qt.ShortcutContext.WidgetShortcut
CHILDREN = Qt.ShortcutContext.WidgetWithChildrenShortcut
WINDOW = Qt.ShortcutContext.WindowShortcut
APP = Qt.ShortcutContext.ApplicationShortcut


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        # Ancestor workspace scope captures keys while focus is inside the plugin
        ((CHILDREN, "workspace"), (CHILDREN, "plugin"), True),
        ((CHILDREN, "workspace"), (WIDGET, "plugin_child"), True),
        ((CHILDREN, "plugin"), (CHILDREN, "plugin_child"), True),
        # Sibling subtrees never share focus
        ((CHILDREN, "plugin"), (CHILDREN, "sibling_plugin"), False),
        ((WIDGET, "plugin"), (WIDGET, "sibling_plugin"), False),
        # WidgetShortcut on an ancestor is only active on the ancestor itself
        ((WIDGET, "workspace"), (CHILDREN, "plugin"), False),
        ((WIDGET, "plugin"), (WIDGET, "plugin"), True),
        # Window scope covers every widget of the same top-level window
        ((WINDOW, "sibling_plugin"), (CHILDREN, "plugin"), True),
        ((WINDOW, "other_window"), (CHILDREN, "plugin"), False),
        # Application scope overlaps everything
        ((APP, "other_window"), (WIDGET, "plugin_child"), True),
    ],
)
def test_scopes_overlap_matches_qt_context_semantics(
    tree: dict[str, QWidget],
    first: tuple[Qt.ShortcutContext, str],
    second: tuple[Qt.ShortcutContext, str],
    expected: bool,
) -> None:
    first_id, second_id = unique_id("first"), unique_id("second")
    ShortcutManager.register_shortcut(first_id, lambda: None, tree[first[1]], context=first[0])
    ShortcutManager.register_shortcut(second_id, lambda: None, tree[second[1]], context=second[0])

    assert ShortcutManager.scopes_overlap(first_id, second_id) is expected
    assert ShortcutManager.scopes_overlap(second_id, first_id) is expected


def test_scopes_overlap_context_menu_action_uses_menu_owner(tree: dict[str, QWidget]) -> None:
    menu = QMenu(tree["plugin"])
    action = QAction("Reset", menu)
    menu.addAction(action)
    menu_action_id, workspace_id, sibling_id = unique_id("menu"), unique_id("workspace"), unique_id("sibling")

    ShortcutManager.register_action(menu_action_id, action)
    ShortcutManager.register_shortcut(workspace_id, lambda: None, tree["workspace"])
    ShortcutManager.register_shortcut(sibling_id, lambda: None, tree["sibling_plugin"])

    assert ShortcutManager.scopes_overlap(menu_action_id, workspace_id) is True
    assert ShortcutManager.scopes_overlap(menu_action_id, sibling_id) is False


def test_scopes_overlap_without_live_registration_is_conservative(tree: dict[str, QWidget]) -> None:
    registered_id, unregistered_id = unique_id("registered"), unique_id("unregistered")
    ShortcutManager.register_shortcut(registered_id, lambda: None, tree["plugin"])

    assert ShortcutManager.scopes_overlap(registered_id, unregistered_id) is True
