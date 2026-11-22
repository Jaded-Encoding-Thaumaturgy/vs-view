from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from vsengine import ManagedEnvironment

from tests.harness import HarnessPluginController
from vsview.app.outputs.video import VideoOutput
from vsview.app.settings import SettingsManager
from vsview.app.tools.cropping.plugin import RegionSelectorPlugin
from vsview.types import Frame
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


@pytest.fixture
def harness(
    plugin_controller: HarnessPluginController,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[int], VideoOutput],
) -> HarnessPluginController:
    plugin_controller.env = vpy_env_factory()
    with plugin_controller.env.use():
        plugin_controller.set_current_voutput(create_test_video_output(100))
    return plugin_controller


def test_settings_snapshot_shows_app_settings_without_plugin_settings(harness: HarnessPluginController) -> None:
    assert harness.file_path is not None
    SettingsManager.global_settings.playback.buffer_size = 42
    SettingsManager.get_local_settings(harness.file_path).last_frame = 7

    settings = harness.api.settings

    assert settings.global_.playback.buffer_size == 42
    assert settings.local_.last_frame == 7
    assert hasattr(settings.global_, "plugins") is False
    assert hasattr(settings.local_, "plugins") is False


@pytest.mark.parametrize(
    ("scope", "match"),
    [("unsupported_scope", "Invalid settings scope"), ("local", "no local_settings_model")],
)
def test_update_settings_rejects_a_scope_the_plugin_cannot_store(
    qt_event_loop: QtEventLoop, harness: HarnessPluginController, scope: str, match: str
) -> None:
    plugin = harness.create_plugin(RegionSelectorPlugin)

    with pytest.raises(ValueError, match=match):
        harness.update_settings(plugin, scope)


@pytest.mark.parametrize(("seconds", "expected"), [(0.0, 0), (1.0, 24), (2.5, 60)])
def test_video_output_proxy_converts_time_to_frame(
    harness: HarnessPluginController, seconds: float, expected: int
) -> None:
    assert harness.current_voutput.time_to_frame(timedelta(seconds=seconds)) == Frame(expected)
