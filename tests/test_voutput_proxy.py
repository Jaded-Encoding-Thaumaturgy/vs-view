from __future__ import annotations

import pytest
import vapoursynth as vs

from vsview.app.outputs import VideoMetadata, VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins._interface import _make_voutput_proxy

core = vs.core


@pytest.mark.vpy("initial-core")
def test_make_voutput_proxy_shares_frame_time_sequences() -> None:
    # The proxy is rebuilt on every PluginAPI access (e.g. per table cell),
    # so VideoOutput must store the per-frame sequences as frozen tuples.
    clip = core.std.BlankClip(width=16, height=16, length=4)
    vs_output = vs.VideoOutputTuple(clip=clip, alpha=None, alt_output=0)

    vfr = VideoOutput(vs_output, 0, Packer(), VideoMetadata(name="Clip 0", framedurs=[1.0, 1.0, 1.0, 1.0]))
    cfr = VideoOutput(vs_output, 0, Packer())

    for voutput in (vfr, cfr):
        assert isinstance(voutput.cum_durations, tuple)
        assert isinstance(voutput.midpoints, tuple)

        proxy = _make_voutput_proxy(voutput)

        assert proxy.framedurs is voutput.framedurs
        assert proxy.cum_durations is voutput.cum_durations
        assert proxy.midpoints is voutput.midpoints

    assert isinstance(vfr.framedurs, tuple)
    assert cfr.framedurs is None
