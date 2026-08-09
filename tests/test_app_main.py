from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from vsengine.loops import set_loop

from vsview.app.main import Application, MainWindow, WorkspaceToolButton
from vsview.app.settings.dialog import ShortcutEditor
from vsview.app.settings.models import WindowGeometry
from vsview.app.workspace import PythonScriptWorkspace, VideoFileWorkspace
from vsview.vsenv import QtEventLoop, unregister_policy


@pytest.fixture
def main_window(qapp: QApplication, qtbot: QtBot) -> MainWindow:
    set_loop(QtEventLoop(qapp))

    window = MainWindow()
    window.stack.animations_enabled = False
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture(autouse=True)
def ensure_no_policy() -> Generator[None, None, None]:
    try:
        yield
    finally:
        unregister_policy()


@pytest.mark.vpy("no-policy")
def test_main_window_init(main_window: MainWindow) -> None:
    assert main_window.windowTitle() == "VS View"
    assert main_window.centralWidget()
    assert len(main_window.nav_container.buttons) == 0

    menu_actions = [action.text() for action in main_window.menu_bar.actions()]
    assert menu_actions == ["New", "View", "Settings", "Help"]


@pytest.mark.vpy("no-policy")
def test_event_filter_tab_key_consumption(
    main_window: MainWindow,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Standard widget focus - Tab key should be consumed by eventFilter
    key_event_tab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert main_window.eventFilter(main_window, key_event_tab) is True

    key_event_backtab = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert main_window.eventFilter(main_window, key_event_backtab) is True

    # Non-tab key should not be consumed
    key_event_a = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    assert main_window.eventFilter(main_window, key_event_a) is False

    # Focus on ShortcutEditor - Tab should pass through (not consumed)
    editor = ShortcutEditor(main_window)
    qtbot.addWidget(editor)
    editor.setFocus()

    # Stub focusWidget to return editor
    monkeypatch.setattr(main_window, "focusWidget", lambda: editor)
    assert main_window.eventFilter(main_window, key_event_tab) is False


@pytest.mark.vpy("no-policy")
def test_workspace_lifecycle_add_and_delete(main_window: MainWindow) -> None:
    # Add PythonScriptWorkspace
    btn1 = main_window.add_workspace(PythonScriptWorkspace)
    assert isinstance(btn1, WorkspaceToolButton)
    assert len(main_window.nav_container.buttons) == 1
    assert main_window.stack.count() == 1
    assert main_window.stack.currentWidget() is btn1.workspace

    # Add VideoFileWorkspace
    btn2 = main_window.add_workspace(VideoFileWorkspace)
    assert len(main_window.nav_container.buttons) == 2
    assert main_window.stack.count() == 2
    assert main_window.stack.currentWidget() is btn2.workspace

    # Delete active workspace (btn2) - should switch to btn1
    main_window.delete_workspace(btn2)
    assert len(main_window.nav_container.buttons) == 1
    assert main_window.nav_container.buttons[0] is btn1
    assert main_window.stack.count() == 1
    assert main_window.stack.currentWidget() is btn1.workspace

    # Delete remaining workspace (btn1)
    main_window.delete_workspace(btn1)
    assert len(main_window.nav_container.buttons) == 0
    assert main_window.stack.count() == 0


@pytest.mark.vpy("no-policy")
def test_workspace_delete_cancelled_by_confirm_close(main_window: MainWindow) -> None:
    btn = main_window.add_workspace(PythonScriptWorkspace)

    # Mock confirm_close to return False
    btn.workspace.confirm_close = MagicMock(return_value=False)  # type: ignore[method-assign]

    main_window.delete_workspace(btn)

    # Workspace deletion should be cancelled
    assert len(main_window.nav_container.buttons) == 1
    assert main_window.stack.count() == 1
    btn.workspace.confirm_close.assert_called_once()


@pytest.mark.vpy("no-policy")
def test_restore_geometry_offscreen_fallback(main_window: MainWindow) -> None:
    # Set out-of-bounds coordinates
    main_window.settings_manager.global_settings.window_geometry = WindowGeometry(
        x=-10000,
        y=-10000,
        width=800,
        height=600,
        is_maximized=False,
    )

    main_window._restore_geometry()

    # Window position should be reset into valid primary screen bounds (x >= 0, y >= 0)
    pos = main_window.pos()
    assert pos.x() >= 0
    assert pos.y() >= 0


@pytest.mark.vpy("no-policy")
def test_view_sidebar_toggle(main_window: MainWindow) -> None:
    initial_state = main_window.sidebar.isVisible()

    # Trigger action toggle
    main_window.view_sidebar_action.setChecked(not initial_state)
    main_window._on_view_sidebar_action_triggered(not initial_state)

    assert main_window.sidebar.isVisible() == (not initial_state)
    assert main_window.settings_manager.global_settings.appearance.sidebar_visible == (not initial_state)


@pytest.mark.vpy("no-policy")
def test_draggable_nav_container_move_button(main_window: MainWindow) -> None:
    btn1 = main_window.add_workspace(PythonScriptWorkspace)
    btn2 = main_window.add_workspace(VideoFileWorkspace)

    nav = main_window.nav_container
    assert nav.buttons == [btn1, btn2]

    # Move btn2 to index 0
    nav.move_button(btn2, 0)
    assert nav.buttons == [btn2, btn1]

    # Test drop index calculation
    assert nav._get_drop_index(-10) == 0
    assert nav._get_drop_index(10000) == 2


@pytest.mark.vpy("no-policy")
def test_close_event_clean_teardown(main_window: MainWindow) -> None:
    btn = main_window.add_workspace(PythonScriptWorkspace)
    btn.workspace.confirm_close = MagicMock(return_value=True)  # type: ignore[method-assign]

    event = QCloseEvent()
    main_window.closeEvent(event)

    btn.workspace.confirm_close.assert_called_once()


@pytest.mark.vpy("no-policy")
def test_application_global_settings_changed(main_window: MainWindow, qapp: QApplication) -> None:
    sm = main_window.settings_manager
    original_theme = sm.global_settings.appearance.theme
    target_theme = Qt.ColorScheme.Dark if original_theme != Qt.ColorScheme.Dark else Qt.ColorScheme.Light

    try:
        sm.global_settings.appearance.theme = target_theme
        Application._on_global_settings_changed(qapp)  # type: ignore[arg-type]
        if qapp.styleHints().colorScheme() != Qt.ColorScheme.Unknown:
            assert qapp.styleHints().colorScheme() == target_theme
    finally:
        sm.global_settings.appearance.theme = original_theme
        Application._on_global_settings_changed(qapp)  # type: ignore[arg-type]


@pytest.mark.vpy("no-policy")
def test_workspace_clear_action(main_window: MainWindow) -> None:
    btn1 = main_window.add_workspace(PythonScriptWorkspace)
    btn2 = main_window.add_workspace(VideoFileWorkspace)

    assert main_window.nav_container.buttons == [btn1, btn2]

    # Clear workspace at index 0 (btn1)
    main_window._on_clear_action(btn1)

    # Nav container should still have 2 buttons, with index 0 holding a new PythonScriptWorkspace
    assert len(main_window.nav_container.buttons) == 2
    new_btn = main_window.nav_container.buttons[0]
    assert new_btn is not btn1
    assert isinstance(new_btn.workspace, PythonScriptWorkspace)


@pytest.mark.vpy("no-policy")
def test_stack_switch_clears_old_workspace_cache(main_window: MainWindow) -> None:
    btn1 = main_window.add_workspace(PythonScriptWorkspace)
    btn2 = main_window.add_workspace(VideoFileWorkspace)

    mock_env = MagicMock()
    mock_env.disposed = False
    btn1.workspace._env = mock_env

    # Switch stack to btn1 then to btn2
    main_window.stack.setCurrentWidget(btn1.workspace)
    main_window.stack.setCurrentWidget(btn2.workspace)

    # Leaving btn1 should trigger mock_env.core.clear_cache()
    mock_env.core.clear_cache.assert_called_once()
