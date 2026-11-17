from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

import pytest
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
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class RecordingPlugin(WidgetPluginBase[Any, Any]):
    identifier = "recording_plugin"
    display_name = "Recording Plugin"

    def __init__(self, parent: QWidget | None, api: PluginAPI) -> None:
        super().__init__(parent, api)  # pyright: ignore[reportArgumentType]
        self.events = list[str]()

    @override
    def on_current_frame_changed(self, n: int) -> None:
        self.events.append(f"frame {n}")

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
    ids=["playback_started", "playback_stopped", "frame_changed", "mouse_moved", "key_press"],
)
def test_events_reach_only_plugins_the_user_can_see(
    controller: PluginWorkspaceController,
    plugins: Plugins,
    dispatch: Callable[[PluginWorkspaceController], None],
    event: str,
) -> None:
    dispatch(controller)

    assert events(plugins) == {"visible": [event], "background_tab": [], "collapsed": [], "hidden": []}


@pytest.fixture
def env(vsview_env_factory: Callable[[], ManagedEnvironment]) -> ManagedEnvironment:
    return vsview_env_factory()


@pytest.mark.vpy("no-policy")
def test_each_visible_plugin_view_gets_its_own_frames_from_the_buffer(
    controller: PluginWorkspaceController,
    workspace: MockQObject,
    qtbot: QtBot,
    env: ManagedEnvironment,
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
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
    buffer = FrameBuffer(create_test_video_output(env, 10), env)

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
