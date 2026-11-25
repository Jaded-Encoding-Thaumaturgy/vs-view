from __future__ import annotations

import ctypes
import random
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import vapoursynth as vs

from vsview.app.cache.session import CacheSession


@pytest.fixture
def session(tmp_path: Path) -> Iterator[CacheSession]:
    session = CacheSession(tmp_path / "cache", quota_bytes=1 << 30)
    yield session
    session.close()


@pytest.fixture
def patterned_clip() -> Callable[..., vs.VideoNode]:
    def create(fmt: vs.PresetVideoFormat, width: int, height: int, seed: str = "main") -> vs.VideoNode:
        blank = vs.core.std.BlankClip(format=fmt, width=width, height=height, length=10)

        def fill(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
            out = f.copy()
            rng = random.Random(f"{seed}:{n}")

            for p in range(out.format.num_planes):
                plane_height = out.height >> out.format.subsampling_h if p else out.height
                size = out.get_stride(p) * plane_height
                ctypes.memmove(out.get_write_ptr(p), rng.randbytes(size), size)

            return out

        return vs.core.std.ModifyFrame(blank, blank, fill)

    return create
