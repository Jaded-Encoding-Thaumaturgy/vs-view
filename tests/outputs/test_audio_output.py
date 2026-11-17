from __future__ import annotations

from math import sqrt
from pathlib import Path

import pytest
import vapoursynth as vs

from tests.harness import HarnessPluginController
from vsview.app.outputs.audio import AudioMetadata, AudioOutput
from vsview.app.plugins.manager import PluginManager
from vsview.types import Frame, Time

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("initial-core")]

FL, FR, FC, LFE, BL, BR = (
    vs.AudioChannels.FRONT_LEFT,
    vs.AudioChannels.FRONT_RIGHT,
    vs.AudioChannels.FRONT_CENTER,
    vs.AudioChannels.LOW_FREQUENCY,
    vs.AudioChannels.BACK_LEFT,
    vs.AudioChannels.BACK_RIGHT,
)
SURROUND_5_1 = [FL, FR, FC, LFE, BL, BR]


@pytest.fixture(autouse=True)
def use_plugin_manager(init_plugin_manager: PluginManager) -> None: ...


@pytest.fixture
def stereo_source(fixture_video_file: Path) -> vs.AudioNode:
    """Non-silent stereo float audio, so mixing coefficients can be checked on real samples."""
    if not hasattr(vs.core, "bs"):
        pytest.skip("BestSource is not available")
    return vs.core.bs.AudioSource(str(fixture_video_file))[: AudioOutput.SAMPLES_PER_FRAME * 40]


def samples(node: vs.AudioNode, channel: int, n: int = 30) -> list[float]:
    with node.get_frame(n) as frame:
        return list(frame[channel])


@pytest.mark.parametrize(
    ("channels", "expected"),
    [
        ((FL, FR), "2.0"),
        ((FL, FR, FC, LFE, vs.AudioChannels.SIDE_LEFT, vs.AudioChannels.SIDE_RIGHT), "5.1"),
        ((FL, FR, FC, LFE, BL, BR), "5.1"),
        ((FL, FR, FC), "Unknown"),
    ],
    ids=["stereo", "5.1-side", "5.1-back", "unknown"],
)
def test_audio_output_channels_layout(channels: tuple[vs.AudioChannels, ...], expected: str) -> None:
    node = vs.core.std.BlankAudio(channels=list(channels), length=3072)

    output = AudioOutput(node, 0)

    assert output.channels_layout.pretty_name == expected


@pytest.mark.parametrize(
    ("channels", "downmix", "expected_channels"),
    [
        (SURROUND_5_1, True, [FL, FR]),
        (SURROUND_5_1, False, SURROUND_5_1),
        ([FL, FR], True, [FL, FR]),
        ([FL], True, [FL]),
    ],
    ids=["5.1_downmixed", "5.1_kept", "stereo_kept", "mono_kept"],
)
def test_prepared_audio_is_downmixed_to_stereo_only_for_multichannel_audio(
    plugin_controller: HarnessPluginController,
    channels: list[vs.AudioChannels],
    downmix: bool,
    expected_channels: list[vs.AudioChannels],
) -> None:
    node = vs.core.std.BlankAudio(channels=channels, length=10000)
    output = AudioOutput(node, 0, AudioMetadata(name="Audio", downmix=downmix))

    output.prepare_audio(0.0, plugin_controller.api)

    assert list(output.prepared_audio.channels) == expected_channels
    assert output.prepared_audio.num_samples == node.num_samples
    assert output.qformat.channelCount() == len(expected_channels)


def test_stereo_downmix_mixes_center_and_back_channels_into_each_side(
    plugin_controller: HarnessPluginController, stereo_source: vs.AudioNode
) -> None:
    # Each 5.1 channel carries the source left (L) or right (R), so every coefficient shows in the result.
    surround = stereo_source.std.AudioMix(
        # FL=L, FR=R, FC=R, LFE=L, BL=R, BR=L
        matrix=[1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0],
        channels_out=SURROUND_5_1,
    )
    output = AudioOutput(surround, 0, AudioMetadata(name="Surround", downmix=True))

    output.prepare_audio(0.0, plugin_controller.api)

    coeff = AudioOutput.DOWNMIX_COEFF
    # Each side sums 1.0 (front) + coeff (center) + coeff (back), normalized by its L2 norm. LFE is dropped.
    norm = sqrt(1 + 2 * coeff**2)
    pairs = list(zip(samples(stereo_source, 0), samples(stereo_source, 1), strict=True))
    assert samples(output.prepared_audio, 0) == pytest.approx(
        [(lv + 2 * coeff * rv) / norm for lv, rv in pairs], abs=1e-6
    )
    assert samples(output.prepared_audio, 1) == pytest.approx(
        [(rv + coeff * (rv + lv)) / norm for lv, rv in pairs], abs=1e-6
    )


@pytest.mark.parametrize(("value", "expected"), [(0.75, 0.75), (-0.5, 0.0), (1.5, 1.0)])
def test_audio_output_volume_is_clamped_and_kept_without_a_sink(value: float, expected: float) -> None:
    output = AudioOutput(vs.core.std.BlankAudio(length=3072), 0)

    output.volume = value

    assert output.volume == expected


@pytest.fixture
def prepared_48k(plugin_controller: HarnessPluginController) -> AudioOutput:
    output = AudioOutput(vs.core.std.BlankAudio(length=48000 * 4, samplerate=48000), 0)
    output.prepare_audio(0.0, plugin_controller.api)
    return output


@pytest.mark.parametrize("frame", [0, 1, 15, 62])
def test_audio_output_frame_conversions_round_trip(prepared_48k: AudioOutput, frame: int) -> None:
    assert prepared_48k.frame_to_sample(frame) == frame * AudioOutput.SAMPLES_PER_FRAME
    assert prepared_48k.sample_to_frame(prepared_48k.frame_to_sample(frame)) == Frame(frame)
    assert prepared_48k.time_to_frame(prepared_48k.frame_to_time(frame).total_seconds()) == Frame(frame)


@pytest.mark.parametrize(("seconds", "sample"), [(0.0, 0), (0.5, 24000), (1.0, 48000), (2.25, 108000)])
def test_audio_output_time_and_sample_conversions(prepared_48k: AudioOutput, seconds: float, sample: int) -> None:
    assert prepared_48k.time_to_sample(seconds) == sample
    assert prepared_48k.sample_to_time(sample) == Time(seconds=seconds)
