from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import vapoursynth as vs

from vsview.app.tools.audio_convert.plugin import AudioConvert, GlobalSettings
from vsview.app.views.notifications import Notification

if TYPE_CHECKING:
    from tests.harness import HarnessPluginController

pytestmark = [pytest.mark.unit, pytest.mark.vpy("initial-core")]


@pytest.mark.parametrize(
    ("sample_type", "bits", "expected_bits"),
    [
        (vs.FLOAT, 16, 32),
        (vs.FLOAT, 24, 32),
        (vs.FLOAT, 32, 32),
        (vs.INTEGER, 16, 16),
        (vs.INTEGER, 24, 24),
        (vs.INTEGER, 32, 32),
    ],
)
def test_settings_force_32_bits_for_float(sample_type: vs.SampleType, bits: int, expected_bits: int) -> None:
    settings = GlobalSettings(bits=bits, sample_type=sample_type)

    assert settings.bits == expected_bits
    assert settings.sample_type == sample_type


@pytest.mark.parametrize("bits", [16, 24])
def test_prepare_converts_float_with_a_stored_integer_bit_depth(
    plugin_controller: HarnessPluginController, bits: int
) -> None:
    plugin = AudioConvert(plugin_controller.api)
    plugin.update_global_settings(sample_type=vs.FLOAT, bits=bits)
    audio = vs.core.std.BlankAudio(sampletype=vs.INTEGER, bits=16)

    out = plugin.prepare(audio)

    assert (out.sample_type, out.bits_per_sample) == (vs.FLOAT, 32)


def test_prepare_failure_returns_the_input_and_reports(plugin_controller: HarnessPluginController) -> None:
    plugin = AudioConvert(plugin_controller.api)
    plugin.update_global_settings(sample_type=vs.INTEGER, bits=16, dither_type="bogus")
    audio = vs.core.std.BlankAudio(sampletype=vs.FLOAT, bits=32)
    notifications = list[Notification]()
    plugin_controller.notificationRequested.connect(notifications.append)

    out = plugin.prepare(audio)

    assert out is audio
    assert notifications == [
        Notification(
            "Audio Convert failed, playing the audio unconverted: "
            "AudioResample: invalid dither_type, must be none, rectangular or triangular",
            Notification.Severity.WARNING,
        )
    ]
