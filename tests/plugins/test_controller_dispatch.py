from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

import pytest
import vapoursynth as vs
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot
from vsengine import ManagedEnvironment

from tests.harness import MockQObject
from vsview.app.outputs.buffer import FrameBuffer
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins._controller import PluginWorkspaceController
from vsview.app.plugins.api import PluginAPI, PluginGraphicsView, WidgetPluginBase
from vsview.app.plugins.contracts import VideoOutputProxy
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class RecordingPlugin(WidgetPluginBase[Any, Any]):
    identifier = "recording_plugin"
    display_name = "Recording Plugin"

    def __init__(self, parent: QWidget | None, api: PluginAPI) -> None:
        super().__init__(parent, api)  # pyright: ignore[reportArgumentType]
        self.events = list[str]()

    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        self.events.append("voutput")

    @override
    def on_current_frame_changed(self, n: int) -> None:
        self.events.append(f"frame {n}")

    @override
    def on_frame_displayed(self, n: int) -> None:
        self.events.append(f"displayed {n}")

    @override
    def on_playback_started(self) -> None:
        self.events.append("started")

    @override
    def on_playback_stopped(self) -> None:
        self.events.append("stopped")

    @override
    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        self.events.append("mouse moved")

    @override
    def on_view_key_press(self, event: QKeyEvent) -> None:
        self.events.append("key pressed")


class RaisingPlugin(RecordingPlugin):
    identifier = "raising_plugin"

    @override
    def on_playback_started(self) -> None:
        raise RuntimeError("broken plugin")

    @override
    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        raise RuntimeError("broken plugin")


class FrameRecordingView(PluginGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        self.rendered = list[int]()

    @override
    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        self.rendered.append(n)
        super().on_current_frame_changed(n, f)


class SilentPlugin(WidgetPluginBase[Any, Any]):
    """Plugin that does not override any view event handler."""

    identifier = "silent_plugin"
    display_name = "Silent Plugin"


class Plugins:
    def __init__(self, controller: PluginWorkspaceController, qtbot: QtBot) -> None:
        api = controller.api
        self.raising = RaisingPlugin(None, api)
        self.visible = RecordingPlugin(None, api)
        self.background_tab = RecordingPlugin(None, api)
        self.collapsed = RecordingPlugin(None, api)
        self.hidden = RecordingPlugin(None, api)
        self.silent = SilentPlugin(None, api)  # pyright: ignore[reportArgumentType]

        self.host = QWidget()
        qtbot.addWidget(self.host)
        qtbot.addWidget(self.hidden)

        tabs = QTabWidget()
        tabs.addTab(self.visible, "visible")
        tabs.addTab(self.background_tab, "background")
        splitter = QSplitter()
        splitter.addWidget(self.raising)
        splitter.addWidget(self.collapsed)

        layout = QVBoxLayout(self.host)
        layout.addWidget(tabs)
        layout.addWidget(splitter)
        layout.addWidget(self.silent)
        self.host.resize(400, 400)
        self.host.show()
        qtbot.waitExposed(self.host)
        splitter.setSizes([200, 0])

    @property
    def recording(self) -> list[RecordingPlugin]:
        return [self.raising, self.visible, self.background_tab, self.collapsed, self.hidden]


@pytest.fixture
def workspace(mock_qobject: MockQObject) -> MockQObject:
    mock_qobject.plugins = []
    return mock_qobject


@pytest.fixture
def controller(qt_event_loop: QtEventLoop, workspace: MockQObject) -> PluginWorkspaceController:
    return PluginWorkspaceController(workspace)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def plugins(controller: PluginWorkspaceController, workspace: MockQObject, qtbot: QtBot) -> Plugins:
    plugins = Plugins(controller, qtbot)
    workspace.plugins = [*plugins.recording, plugins.silent]
    controller.update_subscribers()
    return plugins


def events(plugins: Plugins) -> dict[str, list[str]]:
    return {
        "visible": plugins.visible.events,
        "background_tab": plugins.background_tab.events,
        "collapsed": plugins.collapsed.events,
        "hidden": plugins.hidden.events,
    }


@pytest.mark.parametrize(
    ("dispatch", "event"),
    [
        (lambda c: c.on_playback_started(), "started"),
        (lambda c: c.on_playback_stopped(), "stopped"),
        (lambda c: c.on_current_frame_changed(7), "frame 7"),
        (lambda c: c.on_frame_displayed(7), "displayed 7"),
        (
            lambda c: c.on_view_mouse_moved(
                QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(1, 1),
                    QPointF(1, 1),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
            ),
            "mouse moved",
        ),
        (
            lambda c: c.on_view_key_press(
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
            ),
            "key pressed",
        ),
    ],
    ids=["playback_started", "playback_stopped", "frame_changed", "frame_displayed", "mouse_moved", "key_press"],
)
def test_events_reach_only_plugins_the_user_can_see(
    controller: PluginWorkspaceController,
    plugins: Plugins,
    dispatch: Callable[[PluginWorkspaceController], None],
    event: str,
) -> None:
    dispatch(controller)

    assert events(plugins) == {"visible": [event], "background_tab": [], "collapsed": [], "hidden": []}


def test_showing_the_workspace_again_refreshes_the_plugins_that_missed_updates(
    controller: PluginWorkspaceController, workspace: MockQObject, plugins: Plugins
) -> None:
    workspace.isVisible.return_value = False
    controller.on_playback_stopped()
    workspace.isVisible.return_value = True

    controller.on_workspace_shown()

    assert events(plugins) == {"visible": ["voutput"], "background_tab": [], "collapsed": [], "hidden": []}


def test_a_plugin_refreshed_before_the_workspace_is_shown_is_not_refreshed_twice(
    controller: PluginWorkspaceController, workspace: MockQObject, plugins: Plugins
) -> None:
    workspace.isVisible.return_value = False
    controller.on_playback_stopped()
    workspace.isVisible.return_value = True
    controller.init_plugin(plugins.visible)

    controller.on_workspace_shown()

    assert plugins.visible.events == ["voutput"]


def test_showing_the_workspace_without_missed_updates_refreshes_nothing(
    controller: PluginWorkspaceController, plugins: Plugins
) -> None:
    controller.on_playback_stopped()

    controller.on_workspace_shown()

    assert plugins.visible.events == ["stopped"]


@pytest.fixture
def env(vpy_env_factory: Callable[[], ManagedEnvironment]) -> ManagedEnvironment:
    return vpy_env_factory()


@pytest.mark.vpy("no-core")
def test_a_reload_missed_while_hidden_renders_the_plugin_view_again_when_shown(
    controller: PluginWorkspaceController,
    workspace: MockQObject,
    plugins: Plugins,
    qtbot: QtBot,
    env: ManagedEnvironment,
    create_test_video_output: Callable[[int], VideoOutput],
) -> None:
    workspace.env = env
    with workspace.env.use():
        workspace.outputs_manager.current_voutput = create_test_video_output(10)
    workspace.outputs_manager.current_video_index = 0
    workspace.playback.state.current_frame = 3
    view = FrameRecordingView(plugins.visible, controller.api)
    view.show()
    controller.on_current_voutput_changed()
    qtbot.waitUntil(lambda: not view.pixmap_item.pixmap().isNull(), timeout=5000)
    workspace.isVisible.return_value = False
    controller.on_current_voutput_changed(refresh=True)
    workspace.isVisible.return_value = True

    controller.on_workspace_shown()

    qtbot.waitUntil(lambda: view.rendered == [3, 3], timeout=5000)


@pytest.mark.vpy("no-core")
def test_each_visible_plugin_view_gets_its_own_frames_from_the_buffer(
    controller: PluginWorkspaceController,
    workspace: MockQObject,
    qtbot: QtBot,
    env: ManagedEnvironment,
    create_test_video_output: Callable[[int], VideoOutput],
) -> None:
    shown, hidden = RecordingPlugin(None, controller.api), RecordingPlugin(None, controller.api)
    qtbot.addWidget(shown)
    qtbot.addWidget(hidden)
    views = [PluginGraphicsView(plugin, controller.api) for plugin in (shown, shown, shown, hidden)]
    with env.use():
        for view in views:
            view.current_tab = 0
            view.outputs = {0: env.core.std.BlankClip(width=16, height=16, length=10)}
    shown.show()
    qtbot.waitExposed(shown)
    views[2].hide()
    workspace.plugins = [shown, hidden]
    with env.use():
        buffer = FrameBuffer(create_test_video_output(10), env)

    controller.register_plugin_nodes_to_buffer(buffer)
    buffer.allocate(range(3))
    result = buffer.get_next_frame()

    assert result is not None
    _, main_frame, plugin_frames = result
    try:
        assert set(plugin_frames) == {f"recording_plugin:{id(views[0])}", f"recording_plugin:{id(views[1])}"}
    finally:
        main_frame.close()
        for frame in plugin_frames.values():
            frame.close()
        buffer.invalidate()
        buffer.clear()
