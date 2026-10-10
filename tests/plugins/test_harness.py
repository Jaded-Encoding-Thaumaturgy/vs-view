from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from tests.harness import PluginTestHarness
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.exceptions import NoCurrentVideoOutputError
from vsview.app.tools.cropping.plugin import RegionSelectorPlugin
from vsview.types import Frame
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


def test_plugin_harness_lifecycle(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> None:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)

    harness = PluginTestHarness(qtbot, tmp_path, env=env)
    harness.set_current_voutput(voutput)

    # Instantiate RegionSelectorPlugin through the harness
    plugin = harness.create_plugin(RegionSelectorPlugin)
    assert isinstance(plugin, RegionSelectorPlugin)
    assert plugin.api is harness

    # Verify real signals can be connected and triggered
    signal_received = False

    def on_change() -> None:
        nonlocal signal_received
        signal_received = True

    harness.globalSettingsChanged.connect(on_change)
    harness.globalSettingsChanged.emit()
    assert signal_received is True

    # Test rect selection forwarding with real video dimensions (64x64)
    test_rect = QRect(10, 10, 30, 30)
    harness.set_rect_selection(test_rect)
    assert plugin.crop_rect == test_rect

    # Test blocker
    blocker = harness.blocker()
    assert harness.busy is False
    with blocker:
        assert harness.busy is True
    assert harness.busy is False

    # Test destroy callback execution
    destroyed = False

    def on_destroy() -> None:
        nonlocal destroyed
        destroyed = True

    harness.register_on_destroy(on_destroy)
    harness.destroy()
    assert destroyed is True


def test_plugin_harness_explicit_errors(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> None:
    env = vpy_env_factory()
    harness = PluginTestHarness(qtbot, tmp_path)

    # Missing output must raise NoCurrentVideoOutputError
    with pytest.raises(NoCurrentVideoOutputError, match="No video output is currently available"):
        _ = harness.current_time

    with pytest.raises(NoCurrentVideoOutputError, match="No video output is currently available"):
        _ = harness.current_voutput

    # Missing env in vs_context must raise RuntimeError
    with pytest.raises(RuntimeError, match="requires an active, undisposed ManagedEnvironment"), harness.vs_context():
        ...

    # Invalid voutput type must raise TypeError
    with pytest.raises(TypeError, match="Expected VideoOutput or VideoOutputProxy"):
        harness.set_current_voutput(12345)  # type: ignore[arg-type]

    # Plugin creation with env
    harness.env = env
    plugin = harness.create_plugin(RegionSelectorPlugin)

    # Invalid settings scope must raise ValueError
    with pytest.raises(ValueError, match="Invalid settings scope"):
        harness._update_settings(plugin, "unsupported_scope")  # type: ignore[arg-type]

    # Scope without model must raise ValueError
    with pytest.raises(ValueError, match="has no local_settings_model defined"):
        harness._update_settings(plugin, "local")


def test_plugin_harness_frame_and_image_updates(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> None:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)

    harness = PluginTestHarness(qtbot, tmp_path, env=env)
    harness.set_current_voutput(voutput)

    assert harness.current_frame == Frame(0)
    assert harness.current_view.image.isNull() is False

    # Step to frame 5
    harness.set_current_frame(5)
    assert harness.current_frame == Frame(5)
    assert harness.playback.state.current_frame == Frame(5)
    assert 5 in harness.current_voutput.props
    assert harness.current_view.image.isNull() is False
    assert harness.current_time.total_seconds() == pytest.approx(5 / 24, abs=1e-5)
