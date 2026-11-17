from __future__ import annotations

import pytest
import vapoursynth as vs

from vsview.app.outputs.audio import AudioOutput

pytestmark = [pytest.mark.unit, pytest.mark.vpy("initial-core")]

FL, FR, FC, LFE = (
    vs.AudioChannels.FRONT_LEFT,
    vs.AudioChannels.FRONT_RIGHT,
    vs.AudioChannels.FRONT_CENTER,
    vs.AudioChannels.LOW_FREQUENCY,
)


@pytest.mark.parametrize(
    ("channels", "expected"),
    [
        ((FL, FR), "2.0"),
        ((FL, FR, FC, LFE, vs.AudioChannels.SIDE_LEFT, vs.AudioChannels.SIDE_RIGHT), "5.1"),
        ((FL, FR, FC, LFE, vs.AudioChannels.BACK_LEFT, vs.AudioChannels.BACK_RIGHT), "5.1"),
        ((FL, FR, FC), "Unknown"),
    ],
    ids=["stereo", "5.1-side", "5.1-back", "unknown"],
)
def test_audio_output_channels_layout(channels: tuple[vs.AudioChannels, ...], expected: str) -> None:
    node = vs.core.std.BlankAudio(channels=list(channels), length=3072)

    output = AudioOutput(node, 0)

    assert output.channels_layout.pretty_name == expected
