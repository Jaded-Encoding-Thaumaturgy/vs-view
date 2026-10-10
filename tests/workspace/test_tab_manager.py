from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import vapoursynth as vs
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QImage, QKeyEvent, QMouseEvent, QPixmap, QTransform
from PySide6.QtWidgets import QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.app.settings import ActionID, SettingsManager, ShortcutManager
from vsview.app.views import GraphicsView
from vsview.app.views.tab import TabLabel, TabViewWidget
from vsview.app.workspace.tab_manager import PlayHeadToolButton, TabManager

if TYPE_CHECKING:
    from vsengine.policy import ManagedEnvironment

    from tests.harness import PluginTestHarness

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.usefixtures("qt_event_loop")]


@pytest.fixture
def playhead_btn(qtbot: QtBot) -> Generator[PlayHeadToolButton, None, None]:
    """Provide a PlayHeadToolButton whose parent lifecycle is safely managed by qtbot."""
    parent = QWidget()
    qtbot.addWidget(parent)
    btn = PlayHeadToolButton(parent)
    qtbot.addWidget(btn)
    try:
        yield btn
    finally:
        parent.deleteLater()


@pytest.fixture
def tab_manager(qtbot: QtBot, plugin_harness: PluginTestHarness) -> Generator[TabManager, None, None]:
    """Provide a TabManager wired to PluginTestHarness with Qt lifecycle managed by qtbot."""
    parent = QWidget()
    qtbot.addWidget(parent)

    # Attach internal workspace view routing methods expected by TabManager
    for name in (
        "_on_view_context_menu",
        "_on_view_mouse_moved",
        "_on_view_mouse_pressed",
        "_on_view_mouse_released",
        "_on_view_rect_selection_changed",
        "_on_view_rect_selection_finished",
        "_on_view_key_press",
        "_on_view_key_release",
    ):
        if not hasattr(plugin_harness, name):
            setattr(plugin_harness, name, MagicMock())

    mgr = TabManager(parent, plugin_harness)  # pyright: ignore[reportArgumentType]
    qtbot.addWidget(mgr)
    try:
        yield mgr
    finally:
        parent.deleteLater()


@pytest.fixture
def make_video_output(
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> Callable[..., VideoOutput]:
    """Factory fixture returning real VideoOutput domain objects."""
    env = vpy_env_factory()

    def create(name: str = "Test Clip", index: int = 0, hdr: bool = False) -> VideoOutput:
        with env.use():
            clip = env.core.std.BlankClip(
                length=10,
                width=64,
                height=64,
                format=vs.RGB24,
                fpsnum=24,
                fpsden=1,
            )
            clip = env.core.std.SetFrameProps(
                clip,
                _Matrix=vs.MATRIX_RGB,
                _Primaries=vs.PRIMARIES_BT709,
                _Transfer=vs.TRANSFER_BT709,
            )
            packer = Packer()
            packer.hdr = hdr
            voutput = VideoOutput(vs.VideoOutputTuple(clip, None, index), index, packer)  # pyright: ignore[reportArgumentType]
            voutput.vs_name = name
            voutput.prepared_clip = packer.pack_clip(clip, None)
            return voutput

    return create


# 1. PlayHeadToolButton Tests
def test_playhead_tool_button_initial_state(playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn

    assert btn.isCheckable() is True
    assert btn.isChecked() is True
    assert btn.state == PlayHeadToolButton.State.LINK_ADAPT
    assert btn.modes_order == (
        PlayHeadToolButton.State.LINK_ADAPT,
        PlayHeadToolButton.State.LINK_TIME,
        PlayHeadToolButton.State.LINK_FRAME,
        PlayHeadToolButton.State.UNLINK,
    )

    tooltip = btn.toolTip()
    assert "Sync playhead between tabs." in tooltip
    assert f"Current mode: {PlayHeadToolButton.State.LINK_ADAPT.description}" in tooltip
    assert f"[x] {PlayHeadToolButton.State.LINK_ADAPT.description}" in tooltip
    assert f"[ ] {PlayHeadToolButton.State.LINK_TIME.description}" in tooltip
    assert f"[ ] {PlayHeadToolButton.State.LINK_FRAME.description}" in tooltip
    assert f"[ ] {PlayHeadToolButton.State.UNLINK.description}" in tooltip


def test_playhead_tool_button_forward_cycle_set_state(qtbot: QtBot, playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn

    expected_cycle = [
        (PlayHeadToolButton.State.LINK_TIME, True),
        (PlayHeadToolButton.State.LINK_FRAME, True),
        (PlayHeadToolButton.State.UNLINK, False),
        (PlayHeadToolButton.State.LINK_ADAPT, True),
    ]

    for expected_state, expected_checked in expected_cycle:
        with qtbot.waitSignal(btn.stateChanged) as blocker:
            btn.set_state()
        assert blocker.args == [expected_state]
        assert btn.state == expected_state
        assert btn.isChecked() is expected_checked


def test_playhead_tool_button_forward_cycle_left_click(qtbot: QtBot, playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn
    btn.show()

    transitions = [
        (PlayHeadToolButton.State.LINK_TIME, True),
        (PlayHeadToolButton.State.LINK_FRAME, True),
        (PlayHeadToolButton.State.UNLINK, False),
        (PlayHeadToolButton.State.LINK_ADAPT, True),
    ]

    for expected_state, expected_checked in transitions:
        with qtbot.waitSignal(btn.stateChanged) as blocker:
            qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
        assert blocker.args == [expected_state]
        assert btn.state == expected_state
        assert btn.isChecked() is expected_checked


def test_playhead_tool_button_backward_cycle_right_click(qtbot: QtBot, playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn
    btn.show()

    # Initial state is LINK_ADAPT (1).
    # Backward transitions: (state - 1) % 4
    # 1 -> 0 (UNLINK)
    # 0 -> 3 (LINK_FRAME)
    # 3 -> 2 (LINK_TIME)
    # 2 -> 1 (LINK_ADAPT)
    backward_transitions = [
        (PlayHeadToolButton.State.UNLINK, False),
        (PlayHeadToolButton.State.LINK_FRAME, True),
        (PlayHeadToolButton.State.LINK_TIME, True),
        (PlayHeadToolButton.State.LINK_ADAPT, True),
    ]

    for expected_state, expected_checked in backward_transitions:
        with qtbot.waitSignal(btn.stateChanged) as blocker:
            qtbot.mouseClick(btn, Qt.MouseButton.RightButton)  # type: ignore[no-untyped-call]
        assert blocker.args == [expected_state]
        assert btn.state == expected_state
        assert btn.isChecked() is expected_checked


def test_playhead_tool_button_explicit_set_state(qtbot: QtBot, playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn

    with qtbot.waitSignal(btn.stateChanged) as blocker:
        btn.set_state(state=PlayHeadToolButton.State.LINK_FRAME)
    assert blocker.args == [PlayHeadToolButton.State.LINK_FRAME]
    assert btn.state == PlayHeadToolButton.State.LINK_FRAME
    assert btn.isChecked() is True

    with qtbot.waitSignal(btn.stateChanged) as blocker:
        btn.set_state(state=PlayHeadToolButton.State.UNLINK)
    assert blocker.args == [PlayHeadToolButton.State.UNLINK]
    assert btn.state == PlayHeadToolButton.State.UNLINK
    assert btn.isChecked() is False


def test_playhead_tool_button_tooltip_formatting_all_states(
    playhead_btn: PlayHeadToolButton,
) -> None:
    btn = playhead_btn

    for state in PlayHeadToolButton.State:
        btn.set_state(state=state)
        lines = btn.toolTip().split("\n")
        assert lines[0] == "Sync playhead between tabs."
        assert lines[1] == f"Current mode: {state.description}"
        assert lines[2] == "Click to cycle modes:"

        mode_lines = lines[3:]
        assert len(mode_lines) == len(btn.modes_order)
        for mode, line in zip(btn.modes_order, mode_lines, strict=True):
            if mode == state:
                assert line == f"[x] {mode.description}"
            else:
                assert line == f"[ ] {mode.description}"


def test_playhead_tool_button_invalid_state_fallback(
    playhead_btn: PlayHeadToolButton, caplog: pytest.LogCaptureFixture
) -> None:
    btn = playhead_btn

    btn.set_state(state=PlayHeadToolButton.State.LINK_FRAME)
    assert btn.state == PlayHeadToolButton.State.LINK_FRAME

    with caplog.at_level("WARNING"):
        btn.set_state(state=999)

    assert "Unknown play head state 999" in caplog.text
    assert btn.state == PlayHeadToolButton.State.LINK_ADAPT
    assert btn.isChecked() is True


def test_playhead_tool_button_reload_icons(playhead_btn: PlayHeadToolButton) -> None:
    btn = playhead_btn

    for state in PlayHeadToolButton.State:
        btn.set_state(state=state)
        btn.reload_icons()
        assert not btn.icon().isNull()


# 2. TabManager Initialization & Shortcuts Tests
def test_tab_manager_initialization(tab_manager: TabManager, plugin_harness: PluginTestHarness) -> None:
    mgr = tab_manager

    assert mgr.api is plugin_harness
    assert isinstance(mgr.parentWidget(), QWidget)

    # Buttons existence and checked states
    assert isinstance(mgr.sync_playhead_btn, PlayHeadToolButton)
    assert mgr.sync_playhead_btn.isChecked() is True
    assert mgr.sync_playhead_state == PlayHeadToolButton.State.LINK_ADAPT

    assert mgr.sync_zoom_btn.isCheckable() is True
    assert mgr.sync_zoom_btn.isChecked() is True
    assert mgr.is_sync_zoom_enabled is True

    assert mgr.sync_scroll_btn.isCheckable() is True
    assert mgr.sync_scroll_btn.isChecked() is True
    assert mgr.is_sync_scroll_enabled is True

    assert mgr.autofit_btn.isCheckable() is True
    assert mgr.autofit_btn.isChecked() is False

    assert mgr.toggle_tooldock_btn.isCheckable() is True
    assert mgr.toggle_tooldock_btn.isChecked() is False

    assert mgr.toggle_toolpanel_btn.isCheckable() is True
    assert mgr.toggle_toolpanel_btn.isChecked() is False

    assert mgr.disable_switch is True

    # Tab container
    assert isinstance(mgr.tabs, TabViewWidget)
    assert mgr.tabs.cornerWidget(Qt.Corner.TopRightCorner) is mgr.sync_container


def test_tab_manager_shortcuts_registration(tab_manager: TabManager) -> None:
    mgr = tab_manager

    sm = ShortcutManager()
    expected_actions = [
        ActionID.TOGGLE_PLUGIN_DOCK,
        ActionID.SYNC_PLAYHEAD,
        ActionID.SYNC_ZOOM,
        ActionID.SYNC_SCROLL,
        ActionID.AUTOFIT_ALL_VIEWS,
        ActionID.TOGGLE_PLUGIN_PANEL,
    ]

    for aid in expected_actions:
        assert aid.value in sm._shortcuts
        assert any(sc.parent() is mgr for sc in sm._shortcuts[aid.value])

    # Trigger registered shortcuts and verify widget state toggling
    for sc in sm._shortcuts[ActionID.SYNC_ZOOM.value]:
        sc.activated.emit()
    assert mgr.sync_zoom_btn.isChecked() is False

    for sc in sm._shortcuts[ActionID.AUTOFIT_ALL_VIEWS.value]:
        sc.activated.emit()
    assert mgr.autofit_btn.isChecked() is True


# TabManager.create_tabs Tests
def test_create_tabs_single_output_disables_sync(
    tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]
) -> None:
    mgr = tab_manager

    out0 = make_video_output("Main Output", 0, hdr=False)
    tabs = mgr.create_tabs([out0])

    assert tabs.count() == 1
    assert mgr.sync_playhead_btn.isEnabled() is False
    assert mgr.sync_zoom_btn.isEnabled() is False
    assert mgr.sync_scroll_btn.isEnabled() is False
    assert mgr.autofit_btn.isEnabled() is False

    view = tabs.view(0)
    assert isinstance(view, GraphicsView)

    label = tabs.get_tab_label(0)
    assert isinstance(label, TabLabel)
    assert label.name == "Main Output"
    assert label.vs_index == 0


@pytest.mark.vpy("no-core")
def test_create_tabs_multiple_outputs_enables_sync(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    outputs = [
        make_video_output("Output A", 0, hdr=False),
        make_video_output("Output B", 1, hdr=True),
        make_video_output("Output C", 2, hdr=False),
    ]

    tabs = mgr.create_tabs(outputs)

    assert tabs.count() == 3
    assert mgr.sync_playhead_btn.isEnabled() is True
    assert mgr.sync_zoom_btn.isEnabled() is True
    assert mgr.sync_scroll_btn.isEnabled() is True
    assert mgr.autofit_btn.isEnabled() is True

    for i, out in enumerate(outputs):
        view = tabs.view(i)
        assert isinstance(view, GraphicsView)
        label = tabs.get_tab_label(i)
        assert label.name == out.vs_name
        assert label.vs_index == out.vs_index


@pytest.mark.vpy("no-core")
def test_create_tabs_signal_forwarding(
    qtbot: QtBot,
    tab_manager: TabManager,
    plugin_harness: PluginTestHarness,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    out0 = make_video_output("Clip", 0)
    tabs = mgr.create_tabs([out0])
    view = tabs.view(0)

    # API mouse/keyboard/context signals
    context_event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(10, 10), QPoint(10, 10))
    view.contextMenuRequested.emit(context_event)
    plugin_harness._on_view_context_menu.assert_called_once_with(context_event)  # pyright: ignore[reportAttributeAccessIssue]

    mouse_move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mouseMoved.emit(mouse_move_event)
    plugin_harness._on_view_mouse_moved.assert_called_once_with(mouse_move_event)  # pyright: ignore[reportAttributeAccessIssue]

    mouse_press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mousePressed.emit(mouse_press_event)
    plugin_harness._on_view_mouse_pressed.assert_called_once_with(mouse_press_event)  # pyright: ignore[reportAttributeAccessIssue]

    mouse_release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mouseReleased.emit(mouse_release_event)
    plugin_harness._on_view_mouse_released.assert_called_once_with(mouse_release_event)  # pyright: ignore[reportAttributeAccessIssue]

    key_press_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    view.keyPressed.emit(key_press_event)
    plugin_harness._on_view_key_press.assert_called_once_with(key_press_event)  # pyright: ignore[reportAttributeAccessIssue]

    key_release_event = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    view.keyReleased.emit(key_release_event)
    plugin_harness._on_view_key_release.assert_called_once_with(key_release_event)  # pyright: ignore[reportAttributeAccessIssue]

    sel_rect = QRect(0, 0, 20, 20)
    view.rectSelectionChanged.emit(sel_rect)
    plugin_harness._on_view_rect_selection_changed.assert_called_once_with(sel_rect)  # pyright: ignore[reportAttributeAccessIssue]

    view.rectSelectionFinished.emit(sel_rect)
    plugin_harness._on_view_rect_selection_finished.assert_called_once_with(sel_rect)  # pyright: ignore[reportAttributeAccessIssue]

    # Status loading signals
    with qtbot.waitSignal(mgr.statusLoadingStarted) as blocker:
        view.statusSavingImageStarted.emit("Saving...")
    assert blocker.args == ["Saving..."]

    with qtbot.waitSignal(mgr.statusLoadingFinished) as blocker:
        view.statusSavingImageFinished.emit("Finished.")
    assert blocker.args == ["Finished."]

    # SAR transformed signal
    transform = QTransform()
    transform.scale(1.75, 1.0)
    with qtbot.waitSignal(mgr.sarTransformed) as blocker:
        view.displayTransformChanged.emit(transform)
    assert blocker.args == [1.75]


@pytest.mark.vpy("no-core")
def test_create_tabs_with_real_video_output(
    tab_manager: TabManager,
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> None:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 10)

    mgr = tab_manager
    tabs = mgr.create_tabs([voutput])

    assert tabs.count() == 1
    assert tabs.get_tab_label(0).vs_index == 0


@pytest.mark.vpy("no-core")
def test_swap_tabs(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    new_tabs = mgr.create_tabs(outputs)

    mgr.swap_tabs(new_tabs, 1)

    assert mgr.tabs is new_tabs
    assert mgr.tabs.currentIndex() == 1
    assert mgr.tabs.cornerWidget(Qt.Corner.TopRightCorner) is mgr.sync_container
    assert mgr.current_view is new_tabs.widget(1)


# Tab Switching & api.busy Guard Tests
@pytest.mark.vpy("no-core")
def test_switch_tab_disabled_by_default(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0), make_video_output("B", 1)])
    mgr.swap_tabs(tabs, 0)

    assert mgr.disable_switch is True
    assert mgr.tabs.currentIndex() == 0

    mgr.switch_tab(1)
    assert mgr.tabs.currentIndex() == 0

    mgr.switch_tab(delta=1)
    assert mgr.tabs.currentIndex() == 0


@pytest.mark.vpy("no-core")
def test_switch_tab_enabled(
    qtbot: QtBot,
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1), make_video_output("C", 2)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)
    mgr.disable_switch = False

    with qtbot.waitSignal(mgr.tabChanged) as blocker:
        mgr.switch_tab(1)
    assert blocker.args == [1]
    assert mgr.tabs.currentIndex() == 1

    with qtbot.waitSignal(mgr.tabChanged) as blocker:
        mgr.switch_tab(delta=1)
    assert blocker.args == [2]
    assert mgr.tabs.currentIndex() == 2

    with qtbot.waitSignal(mgr.tabChanged) as blocker:
        mgr.switch_tab(delta=-1)
    assert blocker.args == [1]
    assert mgr.tabs.currentIndex() == 1


@pytest.mark.vpy("no-core")
def test_switch_tab_busy_guard_reverts(
    tab_manager: TabManager,
    plugin_harness: PluginTestHarness,
    make_video_output: Callable[..., VideoOutput],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1), make_video_output("C", 2)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)
    mgr.disable_switch = False

    # Switch to tab 1 while not busy
    plugin_harness.busy = False
    mgr.switch_tab(1)
    assert mgr.tabs.currentIndex() == 1
    assert mgr.tabs.previous_tab_index == 0

    # When api.busy is True, switching to tab 2 must be rejected and reverted
    plugin_harness.busy = True
    with caplog.at_level("WARNING"):
        mgr.switch_tab(2)

    assert "Workspace is busy, cannot switch output" in caplog.text
    # Reverted to previous_tab_index (which was 1, the tab active prior to the rejected switch)
    assert mgr.tabs.currentIndex() == 1


def test_on_tab_changed_negative_index_ignored(qtbot: QtBot, tab_manager: TabManager) -> None:
    mgr = tab_manager

    with qtbot.assertNotEmitted(mgr.tabChanged):
        mgr._on_tab_changed(-1)


# Cross-tab Synchronization Tests
@pytest.mark.vpy("no-core")
def test_zoom_sync_across_non_autofit_tabs(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1), make_video_output("C", 2)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    assert mgr.is_sync_zoom_enabled is True

    # Zoom change on current tab syncs to tab 1 and tab 2
    mgr._on_zoom_changed(2.5)

    assert mgr.tabs.get_tab_label(0).zoom == 2.5
    assert mgr.tabs.view(1).current_zoom == 2.5
    assert mgr.tabs.get_tab_label(1).zoom == 2.5
    assert mgr.tabs.view(2).current_zoom == 2.5
    assert mgr.tabs.get_tab_label(2).zoom == 2.5


@pytest.mark.vpy("no-core")
def test_zoom_sync_skips_autofit_tab(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1), make_video_output("C", 2)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    # Set Tab 2 in autofit mode
    mgr.tabs.view(2).set_autofit(True)
    initial_zoom_2 = mgr.tabs.view(2).current_zoom

    mgr._on_zoom_changed(3.0)

    assert mgr.tabs.get_tab_label(0).zoom == 3.0
    assert mgr.tabs.view(1).current_zoom == 3.0
    assert mgr.tabs.get_tab_label(1).zoom == 3.0
    # Tab 2 in autofit mode must not be modified by zoom sync
    assert mgr.tabs.view(2).current_zoom == initial_zoom_2


@pytest.mark.vpy("no-core")
def test_zoom_sync_disabled_behavior(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    mgr.sync_zoom_btn.setChecked(False)
    assert mgr.is_sync_zoom_enabled is False

    mgr._on_zoom_changed(4.0)

    assert mgr.tabs.get_tab_label(0).zoom == 4.0
    assert mgr.tabs.view(1).current_zoom != 4.0
    assert mgr.tabs.get_tab_label(1).zoom != 4.0


@pytest.mark.vpy("no-core")
def test_on_sync_zoom_toggled_syncs_current_view_zoom(
    tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    mgr.sync_zoom_btn.setChecked(False)
    mgr.current_view.set_zoom(3.5)

    # Toggling sync zoom on should propagate current view zoom to other tabs
    mgr.sync_zoom_btn.setChecked(True)
    assert mgr.tabs.view(1).current_zoom == 3.5
    assert mgr.tabs.get_tab_label(1).zoom == 3.5


@pytest.mark.vpy("no-core")
def test_pan_sync_on_tab_changed(
    qtbot: QtBot,
    tab_manager: TabManager,
    plugin_harness: PluginTestHarness,
    make_video_output: Callable[..., VideoOutput],
    mocker: MockerFixture,
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)
    mgr.disable_switch = False
    plugin_harness.busy = False

    # Setup pixmap on tab 0 (prev_view)
    pix = QPixmap(120, 80)
    pix.fill(Qt.GlobalColor.red)
    mgr.tabs.view(0).set_pixmap(pix)

    view1 = mgr.tabs.view(1)
    spy = mocker.spy(view1, "update_center")

    mgr.switch_tab(1)

    qtbot.waitUntil(lambda: spy.call_count == 1, timeout=2000)
    spy.assert_called_once_with(mgr.tabs.view(0))
    # Dummy pixmap was set on view1 matching prev_view size
    assert view1.pixmap_item.pixmap().size() == pix.size()


@pytest.mark.vpy("no-core")
def test_pan_sync_skipped_when_disabled_or_autofit(
    qtbot: QtBot,
    tab_manager: TabManager,
    plugin_harness: PluginTestHarness,
    make_video_output: Callable[..., VideoOutput],
    mocker: MockerFixture,
) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)
    mgr.disable_switch = False
    plugin_harness.busy = False

    pix = QPixmap(100, 100)
    pix.fill(Qt.GlobalColor.blue)
    mgr.tabs.view(0).set_pixmap(pix)

    # When sync_scroll_btn is disabled
    mgr.sync_scroll_btn.setChecked(False)
    spy1 = mocker.spy(mgr.tabs.view(1), "update_center")
    mgr.switch_tab(1)
    qtbot.wait(50)
    spy1.assert_not_called()

    # When prev_view is autofit
    mgr.sync_scroll_btn.setChecked(True)
    mgr.tabs.view(1).set_pixmap(pix)
    mgr.tabs.view(1).set_autofit(True)
    spy0 = mocker.spy(mgr.tabs.view(0), "update_center")
    mgr.switch_tab(0)
    qtbot.wait(50)
    spy0.assert_not_called()


@pytest.mark.vpy("no-core")
def test_global_autofit_toggle(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    mgr.autofit_btn.setChecked(True)
    for i, view in enumerate(mgr.tabs.views()):
        assert view.autofit is True
        assert mgr.tabs.get_tab_label(i).zoom == 0

    mgr.autofit_btn.setChecked(False)
    for i, view in enumerate(mgr.tabs.views()):
        assert view.autofit is False
        assert mgr.tabs.get_tab_label(i).zoom == view.current_zoom


@pytest.mark.vpy("no-core")
def test_global_autofit_under_reload(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    mgr.tabs.view(0).set_autofit(True)
    mgr._on_global_autofit_changed(enabled=False, under_reload=True)

    assert mgr.tabs.get_tab_label(0).zoom == 0


@pytest.mark.vpy("no-core")
def test_on_autofit_changed_single_view(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    outputs = [make_video_output("A", 0), make_video_output("B", 1)]
    tabs = mgr.create_tabs(outputs)
    mgr.swap_tabs(tabs, 0)

    view1 = mgr.tabs.view(1)
    mgr._on_autofit_changed(view1, True)
    assert mgr.tabs.get_tab_label(1).zoom == 0

    mgr._on_autofit_changed(view1, False)
    assert mgr.tabs.get_tab_label(1).zoom == view1.current_zoom


# Frame Render Update Tests
@pytest.mark.vpy("no-core")
def test_update_current_view_qimage(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.cyan)

    mgr.update_current_view(img)
    pix = mgr.current_view.pixmap_item.pixmap()
    assert pix.width() == 64
    assert pix.height() == 48


@pytest.mark.vpy("no-core")
def test_update_current_view_qpixmap(tab_manager: TabManager, make_video_output: Callable[..., VideoOutput]) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    pix = QPixmap(50, 40)
    pix.fill(Qt.GlobalColor.magenta)

    mgr.update_current_view(pix)
    curr_pix = mgr.current_view.pixmap_item.pixmap()
    assert curr_pix.width() == 50
    assert curr_pix.height() == 40


@pytest.mark.vpy("no-core")
def test_update_current_view_sar_applied(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    pix = QPixmap(32, 32)
    mgr.update_current_view(pix, sar=1.5)
    assert mgr.current_view.sar == 1.5


class ClosableFrame:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.vpy("no-core")
def test_update_current_view_backing_frame_closed(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    frame = ClosableFrame()
    pix = QPixmap(32, 32)
    mgr.update_current_view(pix, backing_frame=frame)  # pyright: ignore[reportArgumentType]

    assert frame.closed is True


def test_update_current_view_empty_tabs_closes_frame(tab_manager: TabManager) -> None:
    mgr = tab_manager

    assert mgr.tabs.currentIndex() == -1

    frame = ClosableFrame()
    pix = QPixmap(32, 32)
    mgr.update_current_view(pix, backing_frame=frame)  # pyright: ignore[reportArgumentType]

    assert frame.closed is True


@pytest.mark.vpy("no-core")
def test_update_current_view_exception_closes_frame(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
    mocker: MockerFixture,
) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    mocker.patch.object(mgr.current_view, "set_pixmap", side_effect=RuntimeError("render failure"))
    frame = ClosableFrame()
    pix = QPixmap(32, 32)

    with pytest.raises(RuntimeError, match="render failure"):
        mgr.update_current_view(pix, backing_frame=frame)  # pyright: ignore[reportArgumentType]

    assert frame.closed is True


# Tab Visibility Tests
def test_tab_visibility_unchecked(tab_manager: TabManager) -> None:
    mgr = tab_manager

    SettingsManager.global_settings.appearance.tab_bar = Qt.CheckState.Unchecked
    mgr.set_tab_visiblity()

    assert mgr.tabs.tabBar().isHidden() is True


def test_tab_visibility_checked(tab_manager: TabManager) -> None:
    mgr = tab_manager

    SettingsManager.global_settings.appearance.tab_bar = Qt.CheckState.Checked
    mgr.set_tab_visiblity()

    assert mgr.tabs.tabBar().isHidden() is False


@pytest.mark.vpy("no-core")
def test_tab_visibility_partially_checked(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
) -> None:
    mgr = tab_manager

    SettingsManager.global_settings.appearance.tab_bar = Qt.CheckState.PartiallyChecked

    # Single tab hides tab bar
    tabs1 = mgr.create_tabs([make_video_output("A", 0)])
    mgr.set_tab_visiblity(tabs1)
    assert tabs1.tabBar().isHidden() is True

    # Multiple tabs shows tab bar
    tabs2 = mgr.create_tabs([make_video_output("A", 0), make_video_output("B", 1)])
    mgr.set_tab_visiblity(tabs2)
    assert tabs2.tabBar().isHidden() is False


def test_tab_visibility_reacts_to_settings_signal(tab_manager: TabManager) -> None:
    mgr = tab_manager

    SettingsManager.global_settings.appearance.tab_bar = Qt.CheckState.Checked
    mgr.set_tab_visiblity()
    assert mgr.tabs.tabBar().isHidden() is False

    SettingsManager.global_settings.appearance.tab_bar = Qt.CheckState.Unchecked
    SettingsManager.signals.globalChanged.emit()

    assert mgr.tabs.tabBar().isHidden() is True


# TabManager Disposal Tests
@pytest.mark.vpy("no-core")
def test_delete_later_blocks_signals_and_cleans_up(
    tab_manager: TabManager,
    make_video_output: Callable[..., VideoOutput],
    mocker: MockerFixture,
) -> None:
    mgr = tab_manager

    tabs = mgr.create_tabs([make_video_output("A", 0)])
    mgr.swap_tabs(tabs, 0)

    spy = mocker.spy(mgr.tabs, "deleteLater")
    mgr.deleteLater()

    assert mgr.tabs.signalsBlocked() is True
    spy.assert_called_once()
