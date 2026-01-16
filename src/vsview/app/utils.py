"""Utility functions for vsview."""

import hashlib
import os
import weakref
from collections import OrderedDict, UserDict
from pathlib import Path

import vapoursynth as vs


def path_to_hash(path: str | os.PathLike[str]) -> str:
    """
    Generate a stable hash from an absolute file path.

    Used to create unique filenames for per-script local settings.

    Args:
        path: The file path to hash.

    Returns:
        A 16-character hexadecimal hash string.
    """
    return hashlib.md5(str(Path(path).resolve()).encode()).hexdigest()[:16]


class LRUCache[K, V](OrderedDict[K, V]):
    def __init__(self, cache_size: int = 10) -> None:
        super().__init__()
        self.cache_size = cache_size

    def __getitem__(self, key: K) -> V:
        val = super().__getitem__(key)
        super().move_to_end(key)

        return val

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        super().move_to_end(key)

        while len(self) > self.cache_size:
            oldkey = next(iter(self))
            super().__delitem__(oldkey)


class VideoFramesCache(UserDict[int, vs.VideoFrame]):
    """Ported back from vstools"""

    def __init__(self, clip: vs.VideoNode, cache_size: int) -> None:
        super().__init__()

        self.clip = weakref.ref(clip)
        self.cache_size = cache_size

        vs.register_on_destroy(self.clear)

    def __setitem__(self, key: int, value: vs.VideoFrame) -> None:
        super().__setitem__(key, value)

        if len(self) > self.cache_size:
            del self[next(iter(self.keys()))]

    def __getitem__(self, key: int) -> vs.VideoFrame:
        if key not in self and (c := self.clip()):
            self.add_frame(key, c.get_frame(key))

        return super().__getitem__(key)

    def add_frame(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        f = f.copy()
        self[n] = f
        return f

    def get_frame(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        return self[n]


def cache_clip(clip: vs.VideoNode, cache_size: int) -> vs.VideoNode:
    """Ported back from vstools"""

    cache = VideoFramesCache(clip, cache_size)

    blank = clip.std.BlankClip(keep=True)

    to_cache_node = vs.core.std.ModifyFrame(blank, clip, cache.add_frame)
    from_cache_node = vs.core.std.ModifyFrame(blank, blank, cache.get_frame)

    return vs.core.std.FrameEval(blank, lambda n: from_cache_node if n in cache else to_cache_node)
