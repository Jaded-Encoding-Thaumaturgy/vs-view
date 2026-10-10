from __future__ import annotations

from fractions import Fraction

import pytest
import vapoursynth as vs

from vsview.app.outputs.video import VideoMetadata, VideoOutput
from vsview.app.packing import Packer
from vsview.types import Frame, Time

core = vs.core

pytestmark = [pytest.mark.unit, pytest.mark.vpy("initial-core")]


def test_vfr_time_to_frame_midpoints() -> None:
    # 5 frames, durations: [0.040, 0.040, 0.040, 0.040, 0.040]
    # Frame 0: start 0.000, end 0.040, mid to frame 1 = 0.020
    # Frame 1: start 0.040, end 0.080, mid to frame 2 = 0.060
    # Frame 2: start 0.080, end 0.120, mid to frame 3 = 0.100
    clip = core.std.BlankClip(length=5, fpsnum=0, fpsden=1)
    vo = VideoOutput(
        vs.VideoOutputTuple(clip, None, 0),
        0,
        Packer(),
        VideoMetadata("test", framedurs=[0.040] * 5),
    )

    assert isinstance(vo.midpoints, tuple)
    assert len(vo.midpoints) == 4
    assert vo.midpoints == pytest.approx((0.020, 0.060, 0.100, 0.140))

    # Start timestamps should map to their respective frames
    assert vo.time_to_frame(Time(seconds=0.000)) == Frame(0)
    assert vo.time_to_frame(Time(seconds=0.040)) == Frame(1)
    assert vo.time_to_frame(Time(seconds=0.080)) == Frame(2)

    # Acceptance interval for frame 1 is [0.020, 0.060)
    assert vo.time_to_frame(Time(seconds=0.019)) == Frame(0)
    assert vo.time_to_frame(Time(seconds=0.020)) == Frame(1)
    assert vo.time_to_frame(Time(seconds=0.039)) == Frame(1)
    assert vo.time_to_frame(Time(seconds=0.041)) == Frame(1)
    assert vo.time_to_frame(Time(seconds=0.059)) == Frame(1)
    assert vo.time_to_frame(Time(seconds=0.060)) == Frame(2)


def test_vfr_issue_227_drift_regression() -> None:
    """
    Regression test for Issue #227:
    Switching between two VFR outputs with different frame rates must not drift backwards.
    """
    out0_starts = [10.477, 10.511, 10.544, 10.577, 10.611, 10.644, 10.680]
    durs0 = [out0_starts[i + 1] - out0_starts[i] for i in range(len(out0_starts) - 1)]

    out1_starts = [10.473, 10.494, 10.515, 10.536, 10.556, 10.577, 10.598, 10.619, 10.640, 10.680]
    durs1 = [out1_starts[i + 1] - out1_starts[i] for i in range(len(out1_starts) - 1)]

    clip0 = core.std.BlankClip(length=len(durs0), fpsnum=0, fpsden=1)
    vo0 = VideoOutput(
        vs.VideoOutputTuple(clip0, None, 0),
        0,
        Packer(),
        VideoMetadata("0", framedurs=durs0),
    )

    clip1 = core.std.BlankClip(length=len(durs1), fpsnum=0, fpsden=1)
    vo1 = VideoOutput(
        vs.VideoOutputTuple(clip1, None, 0),
        1,
        Packer(),
        VideoMetadata("1", framedurs=durs1),
    )

    # Start at frame 5 (10.644 in Issue 227)
    f0 = 5
    for _ in range(20):
        t0 = vo0.frame_to_time(f0)
        f1 = vo1.time_to_frame(t0)
        t1 = vo1.frame_to_time(f1)
        f0_next = vo0.time_to_frame(t1)
        assert f0_next == f0, f"Frame drifted from {f0} to {f0_next}"
        f0 = f0_next


def test_cfr_cround_vs_midpoints_equivalence() -> None:
    fps = Fraction(24000, 1001)
    clip = core.std.BlankClip(length=100, fpsnum=fps.numerator, fpsden=fps.denominator)
    vo = VideoOutput(
        vs.VideoOutputTuple(clip, None, 0),
        0,
        Packer(),
        VideoMetadata("cfr"),
    )

    # For CFR without framedurs, time_to_frame uses cround
    for frame in range(100):
        t = vo.frame_to_time(frame)
        assert vo.time_to_frame(t) == Frame(frame)

        # Perturbations within +/- 0.4 frame duration should still map to the same frame
        frame_dur = 1 / fps
        t_early = Time(seconds=t.total_seconds() - 0.4 * float(frame_dur))
        t_late = Time(seconds=t.total_seconds() + 0.4 * float(frame_dur))

        if frame > 0:
            assert vo.time_to_frame(t_early) == Frame(frame)
        if frame < 99:
            assert vo.time_to_frame(t_late) == Frame(frame)


def test_vfr_with_nominal_fps() -> None:
    """When framedurs is provided, it should be used even if clip.fps > 0."""
    durs = [0.030, 0.050, 0.030, 0.050]
    clip = core.std.BlankClip(length=4, fpsnum=24, fpsden=1)  # Nominal 24 fps
    vo = VideoOutput(
        vs.VideoOutputTuple(clip, None, 0),
        0,
        Packer(),
        VideoMetadata("vfr_with_fps", framedurs=durs),
    )

    assert isinstance(vo.midpoints, tuple)
    assert vo.midpoints == pytest.approx((0.015, 0.055, 0.095))
    # Cumulative starts: 0.000, 0.030, 0.080, 0.110
    assert vo.frame_to_time(0) == Time(seconds=0.000)
    assert vo.frame_to_time(1) == Time(seconds=0.030)
    assert vo.frame_to_time(2) == Time(seconds=0.080)
    assert vo.frame_to_time(3) == Time(seconds=0.110)
