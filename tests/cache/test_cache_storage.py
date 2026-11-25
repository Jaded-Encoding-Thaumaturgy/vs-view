from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import vapoursynth as vs

from vsview.app.cache.storage import CacheFrameError, CacheSession, DiskFrameStore, QuotaExceededError, RenderCacheError

core = vs.core

pytestmark = [pytest.mark.unit, pytest.mark.vpy("initial-core")]


def _planes(frame: vs.VideoFrame) -> list[bytes]:
    return [bytes(frame[p]) for p in range(frame.format.num_planes)]


@pytest.fixture
def gray_clip(patterned_clip: Callable[..., vs.VideoNode]) -> vs.VideoNode:
    return patterned_clip(vs.GRAY8, 8, 8)


@pytest.fixture
def gray_store(session: CacheSession, gray_clip: vs.VideoNode) -> DiskFrameStore:
    return session.create_store(gray_clip)


@pytest.mark.parametrize(
    ("fmt", "width", "height"),
    [
        (vs.GRAY8, 64, 32),
        (vs.YUV420P8, 62, 34),
        (vs.YUV420P10, 62, 34),
        (vs.YUV444P16, 30, 10),
        (vs.RGBS, 30, 10),
    ],
)
def test_written_frame_reads_back_with_identical_planes(
    patterned_clip: Callable[..., vs.VideoNode],
    session: CacheSession,
    fmt: vs.PresetVideoFormat,
    width: int,
    height: int,
) -> None:
    clip = patterned_clip(fmt, width, height)
    store = session.create_store(clip)
    source = clip.get_frame(2)

    store.write(2, source)
    frame = store.read(2)

    assert _planes(frame) == _planes(source)
    assert dict(frame.props) == {"_DurationNum": 1, "_DurationDen": 24}


def test_props_survive_except_frame_references(session: CacheSession, gray_clip: vs.VideoNode) -> None:
    clip = core.std.SetFrameProps(
        gray_clip,
        i=1,
        arr=[1, 2, 3],
        f=1.5,
        farr=[1.0, 2.0],
        s="héllo",
        b=b"\x00\xff",
        sarr=["a", "b"],
        _Range=vs.RANGE_LIMITED,
    )
    ref_frame = gray_clip.get_frame(0)

    def add_frame_ref(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        out = f.copy()
        out.props["frame_ref"] = ref_frame
        return out

    clip = core.std.ModifyFrame(clip, clip, add_frame_ref)
    store = session.create_store(clip)

    store.write(0, clip.get_frame(0))
    frame = store.read(0)

    assert dict(frame.props) == {
        "_DurationNum": 1,
        "_DurationDen": 24,
        "i": 1,
        "arr": [1, 2, 3],
        "f": 1.5,
        "farr": [1.0, 2.0],
        "s": "héllo",
        "b": b"\x00\xff",
        "sarr": ["a", "b"],
        "_Range": vs.RANGE_LIMITED,
    }


@pytest.mark.parametrize(
    ("fmt", "width"),
    [pytest.param(vs.GRAY8, 16, id="other-size"), pytest.param(vs.GRAY16, 8, id="other-format")],
)
def test_write_rejects_frames_that_dont_match_the_store(
    patterned_clip: Callable[..., vs.VideoNode],
    gray_store: DiskFrameStore,
    fmt: vs.PresetVideoFormat,
    width: int,
) -> None:
    with pytest.raises(ValueError, match="format and size"):
        gray_store.write(0, patterned_clip(fmt, width, 8).get_frame(0))

    assert gray_store.frame_count == 0
    assert list(gray_store.path.iterdir()) == []


def test_frame_already_cached_is_not_written_again(gray_store: DiskFrameStore, gray_clip: vs.VideoNode) -> None:
    first = gray_clip.get_frame(0)

    gray_store.write(0, first)
    gray_store.write(0, gray_clip.get_frame(1))
    frame = gray_store.read(0)

    assert _planes(frame) == _planes(first)


def test_read_of_a_frame_that_is_not_cached_fails(gray_store: DiskFrameStore) -> None:
    with pytest.raises(CacheFrameError, match="not cached"):
        gray_store.read(3)


def _truncate(path: Path) -> None:
    path.write_bytes(path.read_bytes()[:-10])


def _break_magic(path: Path) -> None:
    path.write_bytes(b"XXXX" + path.read_bytes()[4:])


@pytest.mark.parametrize("damage", [_truncate, _break_magic, Path.unlink])
def test_damaged_frame_is_removed_from_the_cache(
    session: CacheSession,
    gray_store: DiskFrameStore,
    gray_clip: vs.VideoNode,
    damage: Callable[[Path], Any],
) -> None:
    gray_store.write(0, gray_clip.get_frame(0))
    damage(next(gray_store.path.iterdir()))

    with pytest.raises(CacheFrameError, match="can't be read"):
        gray_store.read(0)

    assert 0 not in gray_store
    assert gray_store.size_bytes == 0
    assert session.used_bytes == 0


def test_size_accounting_matches_the_files_on_disk(
    session: CacheSession, gray_store: DiskFrameStore, gray_clip: vs.VideoNode
) -> None:
    gray_store.write(0, gray_clip.get_frame(0))
    gray_store.write(1, gray_clip.get_frame(1))

    on_disk = sum(path.stat().st_size for path in gray_store.path.iterdir())

    assert gray_store.size_bytes == on_disk
    assert session.used_bytes == on_disk


def test_write_over_the_quota_leaves_nothing(tmp_path: Path, gray_clip: vs.VideoNode) -> None:
    session = CacheSession(tmp_path / "cache", quota_bytes=32)
    store = session.create_store(gray_clip)

    with pytest.raises(QuotaExceededError, match="quota"):
        store.write(0, gray_clip.get_frame(0))

    assert 0 not in store
    assert list(store.path.iterdir()) == []
    assert session.used_bytes == 0
    session.close()


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        ([], []),
        ([3], [(3, 3)]),
        ([0, 1, 2, 5, 7, 8], [(0, 2), (5, 5), (7, 8)]),
        ([8, 7, 0, 1], [(0, 1), (7, 8)]),
    ],
)
def test_runs_group_consecutive_frames(
    gray_store: DiskFrameStore, gray_clip: vs.VideoNode, frames: list[int], expected: list[tuple[int, int]]
) -> None:
    for n in frames:
        gray_store.write(n, gray_clip.get_frame(n))

    assert gray_store.runs() == expected


def test_closed_store_deletes_its_directory_and_refuses_writes(
    session: CacheSession, gray_store: DiskFrameStore, gray_clip: vs.VideoNode
) -> None:
    gray_store.write(0, gray_clip.get_frame(0))

    gray_store.close()

    assert not gray_store.path.exists()
    assert session.used_bytes == 0
    with pytest.raises(RenderCacheError, match="closed"):
        gray_store.write(1, gray_clip.get_frame(1))


def test_session_close_deletes_the_session(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    session = CacheSession(root, quota_bytes=1 << 30)

    session.close()

    assert list(root.iterdir()) == []
