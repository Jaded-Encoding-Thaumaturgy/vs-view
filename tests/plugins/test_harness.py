from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from tests.harness import HarnessPluginController
from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.api import PluginAPI
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

    harness = HarnessPluginController(qtbot, tmp_path, env=env)
    harness.set_current_voutput(voutput)

    # Instantiate RegionSelectorPlugin through the harness
    plugin = harness.create_plugin(RegionSelectorPlugin)
    assert isinstance(plugin, RegionSelectorPlugin)
    assert plugin.api is harness.api
    assert isinstance(plugin.api, PluginAPI)

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
    assert harness.api.busy is False
    with blocker:
        assert harness.api.busy is True
    assert harness.api.busy is False

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
    harness = HarnessPluginController(qtbot, tmp_path)

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
        harness.update_settings(plugin, "unsupported_scope")  # type: ignore[arg-type]

    # Scope without model must raise ValueError
    with pytest.raises(ValueError, match="has no local_settings_model defined"):
        harness.update_settings(plugin, "local")  # type: ignore[arg-type]


def test_plugin_harness_frame_and_image_updates(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> None:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)

    harness = HarnessPluginController(qtbot, tmp_path, env=env)
    harness.set_current_voutput(voutput)

    assert harness.api.current_frame == Frame(0)
    assert harness.api.current_view.image.isNull() is False

    # Step to frame 5
    harness.set_current_frame(5)
    assert harness.current_frame == Frame(5)
    assert harness.playback_proxy._state.current_frame == Frame(5)
    assert 5 in harness.current_voutput.props
    assert harness.api.current_view.image.isNull() is False
    assert harness.current_time.total_seconds() == pytest.approx(5 / 24, abs=1e-5)


def test_plugin_harness_extended_capabilities(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
    create_test_audio_output: Callable[[ManagedEnvironment, int], AudioOutput],
) -> None:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)
    aoutput = create_test_audio_output(env, 100)

    harness = HarnessPluginController(qtbot, tmp_path, env=env)
    harness.set_current_voutput(voutput)

    # Audio output tracking
    assert harness.current_aoutput is None
    assert harness.aoutputs == []
    a_proxy = harness.set_current_aoutput(aoutput)
    assert harness.current_aoutput is a_proxy
    assert harness.aoutputs == [a_proxy]

    # Rect selection and clearing
    harness.set_rect_selection(QRect(5, 5, 20, 20))
    assert harness.api.current_view.rect_selection == QRect(5, 5, 20, 20)
    harness.clear_rect_selection()
    assert harness.api.current_view.rect_selection == QRect()

    # Timeline notch tracking
    assert harness.api.timeline.mode == "frame"
    harness.api.timeline.add_notch("test_group", Frame(10), label="Test Notch")
    assert len(harness.timeline_proxy._notches["test_group"]) == 1
    harness.api.timeline.clear_notches("test_group")
    assert "test_group" not in harness.timeline_proxy._notches

    # Playback synchronization and seeking
    assert harness.api.is_playing is False
    assert harness.playback_proxy._state.is_playing is False

    harness.playback_proxy._state.is_playing = True
    assert harness.api.is_playing is True

    # Seeking via frame and timedelta
    assert harness.api.playback.seek(42) is True
    assert harness.current_frame == Frame(42)

    assert harness.api.playback.seek(timedelta(seconds=1.0)) is True
    assert harness.current_frame == Frame(24)

    # Blocker non-blocking behavior
    blocker = harness.blocker()
    assert blocker.acquire(block=True) is True
    assert blocker.locked is True
    assert harness.api.busy is True
    assert blocker.acquire(block=False) is False
    blocker.release()
    assert blocker.locked is False
    assert harness.api.busy is False

    # Settings SimpleNamespace proxy
    assert hasattr(harness.settings, "global_")
    assert hasattr(harness.settings, "local_")
