from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import vapoursynth as vs

from vsview.app.cache import OutputCache
from vsview.app.cache.storage import CacheSession
from vsview.app.outputs import VideoOutput
from vsview.app.packing import Packer
from vsview.app.settings.manager import SettingsManager

pytestmark = [pytest.mark.unit, pytest.mark.vpy("unique-core")]


def _planes(frame: vs.VideoFrame) -> list[bytes]:
    return [bytes(frame[p]) for p in range(frame.format.num_planes)]


def _cached_output(clip: vs.VideoNode) -> tuple[VideoOutput, OutputCache]:
    SettingsManager.global_settings.render_cache.enabled = True
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, Packer())
    assert voutput.cache is not None
    return voutput, voutput.cache


def test_cached_frames_come_from_the_store_and_others_from_the_source(
    session: CacheSession, patterned_clip: Callable[..., vs.VideoNode]
) -> None:
    source = patterned_clip(vs.YUV420P10, 32, 16)
    other = vs.core.std.SetFrameProps(patterned_clip(vs.YUV420P10, 32, 16, seed="other"), origin="disk")
    voutput, cache = _cached_output(source)

    cache.ensure_store(session).write(3, other.get_frame(3))
    cached = voutput.vs_output.clip.get_frame(3)
    live = voutput.vs_output.clip.get_frame(4)

    assert _planes(cached) == _planes(other.get_frame(3))
    assert dict(cached.props) == {"_DurationNum": 1, "_DurationDen": 24, "origin": "disk"}
    assert _planes(live) == _planes(source.get_frame(4))


def test_packed_frame_is_made_from_the_cached_frame(
    session: CacheSession, patterned_clip: Callable[..., vs.VideoNode]
) -> None:
    props = {"_Matrix": vs.MATRIX_RGB, "_Primaries": vs.PRIMARIES_BT709, "_Transfer": vs.TRANSFER_BT709}
    source = vs.core.std.SetFrameProps(patterned_clip(vs.RGB24, 32, 16), **props)
    other = vs.core.std.SetFrameProps(patterned_clip(vs.RGB24, 32, 16, seed="other"), **props)
    voutput, cache = _cached_output(source)
    packer = Packer()

    cache.ensure_store(session).write(5, other.get_frame(5))

    assert _planes(packer.pack_clip(voutput.vs_output.clip, None).get_frame(5)) == _planes(
        packer.pack_clip(other, None).get_frame(5)
    )


def test_unreadable_frame_renders_live(session: CacheSession, patterned_clip: Callable[..., vs.VideoNode]) -> None:
    source = patterned_clip(vs.GRAY8, 16, 16)
    other = patterned_clip(vs.GRAY8, 16, 16, seed="other")
    voutput, cache = _cached_output(source)
    store = cache.ensure_store(session)
    store.write(2, other.get_frame(2))
    next(store.path.iterdir()).unlink()

    frame = voutput.vs_output.clip.get_frame(2)

    assert _planes(frame) == _planes(source.get_frame(2))
    assert 2 not in store


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [pytest.param({"format": vs.GRAY8}, {"format": vs.YUV420P8}, "variable format", id="format")],
)
def test_variable_output_is_not_wrapped(first: dict[str, Any], second: dict[str, Any], reason: str) -> None:
    SettingsManager.global_settings.render_cache.enabled = True

    clips = [vs.core.std.BlankClip(length=1, **first), vs.core.std.BlankClip(length=1, **second)]
    source = vs.core.std.Splice(clips, mismatch=True)

    voutput = VideoOutput(vs.VideoOutputTuple(source, None, 0), 0, Packer())

    assert voutput.cache is None
    assert voutput.cache_unavailable_reason is not None
    assert reason in voutput.cache_unavailable_reason
    assert voutput.vs_output.clip is source


def test_alpha_output_is_not_wrapped(patterned_clip: Callable[..., vs.VideoNode]) -> None:
    SettingsManager.global_settings.render_cache.enabled = True
    source = patterned_clip(vs.RGB24, 32, 16)
    alpha = patterned_clip(vs.GRAY8, 32, 16, seed="alpha")

    voutput = VideoOutput(vs.VideoOutputTuple(source, alpha, 0), 0, Packer())

    assert voutput.cache is None
    assert voutput.cache_unavailable_reason is not None
    assert "alpha" in voutput.cache_unavailable_reason
    assert voutput.vs_output.clip is source
    assert voutput.vs_output.alpha is alpha


def test_clear_closes_the_store(session: CacheSession, patterned_clip: Callable[..., vs.VideoNode]) -> None:
    source = patterned_clip(vs.GRAY8, 16, 16)
    voutput, cache = _cached_output(source)
    store = cache.ensure_store(session)
    store.write(0, source.get_frame(0))

    voutput.clear()

    assert not store.path.exists()
    assert session.used_bytes == 0
    assert voutput.cache is None
