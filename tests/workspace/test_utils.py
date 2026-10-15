from __future__ import annotations

import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import vapoursynth as vs
from pytest_mock import MockerFixture

from vsview.app.utils import VideoFramesCache
from vsview.app.workspace.utils import EXCLUDED_PREFIXES, _get_installed_top_levels, evict_packages, find_local_packages

pytestmark = [pytest.mark.unit]


def _make_module(name: str, file: str | None) -> ModuleType:
    mod = ModuleType(name)
    mod.__file__ = file
    mod.__spec__ = ModuleSpec(name, None, origin=file)
    return mod


def test_get_installed_top_levels_includes_editable_installs(mocker: MockerFixture) -> None:
    mocker.patch("importlib.metadata.packages_distributions", return_value={"pyside6": ["pyside6"]})
    mocker.patch(
        "importlib.metadata.distributions",
        return_value=[type("D", (), {"metadata": {"Name": "vsview-comp"}})()],
    )

    result = _get_installed_top_levels()

    assert "pyside6" in result
    assert "vsview_comp" in result


def test_find_local_packages_detects_local_skips_installed(tmp_path: Path, mocker: MockerFixture) -> None:
    local_file = tmp_path / "localmod.py"
    local_file.write_text("x = 1\n")

    prefix = next(iter(EXCLUDED_PREFIXES))
    stdlib_path = str(prefix / "_asyncio.pyd")

    mocker.patch("vsview.app.workspace.utils._get_installed_top_levels", return_value=frozenset({"pyside6"}))
    mocker.patch.dict(
        sys.modules,
        {
            "localmod": _make_module("localmod", str(local_file)),
            "pyside6": _make_module("pyside6", str(tmp_path / "pyside6.py")),
            "_asyncio": _make_module("_asyncio", stdlib_path),
        },
    )

    result = find_local_packages()

    # Local module is detected
    assert "localmod" in result
    # Installed module is skipped
    assert "pyside6" not in result
    # Stdlib module is skipped
    assert "_asyncio" not in result


def test_evict_packages_removes_submodules_not_partial_matches(mocker: MockerFixture) -> None:
    mocker.patch.dict(
        sys.modules,
        {
            "foo": _make_module("foo", "/tmp/foo/__init__.py"),
            "foo.bar": _make_module("foo.bar", "/tmp/foo/bar.py"),
            "foobar": _make_module("foobar", "/tmp/foobar.py"),
        },
    )

    evict_packages(["foo"])

    # Package and submodules are evicted
    assert "foo" not in sys.modules
    assert "foo.bar" not in sys.modules
    # Unrelated package is not affected
    assert "foobar" in sys.modules


# VideoFramesCache Tests
@pytest.mark.vpy("initial-core")
def test_video_frames_cache_lru_eviction(sample_video_clip: vs.VideoNode) -> None:
    cache = VideoFramesCache(sample_video_clip, cache_size=2)

    # Add frames 0 and 1
    f0 = sample_video_clip.get_frame(0)
    f1 = sample_video_clip.get_frame(1)
    cache[0] = f0
    cache[1] = f1

    assert 0 in cache
    assert 1 in cache
    assert f0.closed is False
    assert f1.closed is False

    # Access frame 0 to make it MRU
    _ = cache[0]

    # Add frame 2
    f2 = sample_video_clip.get_frame(2)
    cache[2] = f2

    # Verify frame 1 was evicted and frame.close() called on it
    assert 1 not in cache
    assert f1.closed is True
    with pytest.raises(RuntimeError, match=r"The Frame has already been released"):
        f1.get_read_ptr(0)

    # Verify frames 0 and 2 remain in cache and open
    assert 0 in cache
    assert 2 in cache
    assert f0.closed is False
    assert f2.closed is False
    assert cache[0] is f0
    assert cache[2] is f2


@pytest.mark.vpy("initial-core")
def test_video_frames_cache_deletion(sample_video_clip: vs.VideoNode) -> None:
    cache = VideoFramesCache(sample_video_clip, cache_size=2)
    f0 = sample_video_clip.get_frame(0)
    cache[0] = f0

    assert 0 in cache
    assert f0.closed is False

    del cache[0]

    assert 0 not in cache
    assert f0.closed is True
    with pytest.raises(RuntimeError, match=r"The Frame has already been released"):
        f0.get_read_ptr(0)
    with pytest.raises(KeyError):
        del cache[0]


@pytest.mark.vpy("initial-core")
def test_video_frames_cache_miss_fetches_from_clip(sample_video_clip: vs.VideoNode) -> None:
    class ClipSpy:
        def __init__(self, clip: vs.VideoNode) -> None:
            self._clip = clip
            self.calls = list[int]()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._clip, name)

        def get_frame(self, n: int) -> vs.VideoFrame:
            self.calls.append(n)
            return self._clip.get_frame(n)

    spy_clip = ClipSpy(sample_video_clip)
    cache = VideoFramesCache(spy_clip, cache_size=5)  # type: ignore[arg-type]

    assert 3 not in cache

    frame = cache[3]

    assert spy_clip.calls == [3]
    assert isinstance(frame, vs.VideoFrame)
    assert frame.closed is False
    assert 3 in cache
    assert cache[3] is frame
    # Cache hit on subsequent access
    assert spy_clip.calls == [3]


@pytest.mark.vpy("initial-core")
def test_video_frames_cache_clear(sample_video_clip: vs.VideoNode) -> None:
    cache = VideoFramesCache(sample_video_clip, cache_size=5)
    f0 = sample_video_clip.get_frame(0)
    f1 = sample_video_clip.get_frame(1)
    cache[0] = f0
    cache[1] = f1

    assert len(cache) == 2
    assert cache.clip is not None

    cache.clear()

    assert len(cache) == 0
    assert 0 not in cache
    assert 1 not in cache
    assert f0.closed is True
    assert f1.closed is True
    with pytest.raises(RuntimeError, match=r"The Frame has already been released"):
        f0.get_read_ptr(0)
    with pytest.raises(RuntimeError, match=r"The Frame has already been released"):
        f1.get_read_ptr(0)
    assert cache.clip is None
    with pytest.raises(KeyError):
        _ = cache[0]
