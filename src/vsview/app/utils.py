"""Utility functions for vsview."""

import gc
import hashlib
import io
import os
import sys
import threading
import weakref
from collections import OrderedDict, UserDict
from collections.abc import Callable, Container, Generator, Iterator, MutableSet, Sized
from contextlib import contextmanager
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Literal, override

import vapoursynth as vs
from PySide6.QtCore import QObject, QTimer
from shiboken6 import Shiboken

logger = getLogger(__name__)


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


class _CheckLeaks[**P, R]:
    def __init__(self, func: Callable[P, R]) -> None:
        self.func = func

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.func(*args, **kwargs)

    @contextmanager
    def ctx(self) -> Generator[None]:
        from ..env import getenv_bool

        try:
            if getenv_bool("VSVIEW_DEBUG"):
                QTimer.singleShot(0, lambda: check_leaks("before"))
            yield
        finally:
            if getenv_bool("VSVIEW_DEBUG"):
                QTimer.singleShot(0, lambda: check_leaks("after"))


@_CheckLeaks
def check_leaks(stage: Literal["before", "after"]) -> None:
    try:
        import objgraph  # type: ignore[import-untyped]
    except ImportError:
        logger.exception("")
        return

    gc.collect()

    # Capture show_growth output
    with io.StringIO() as buf:
        original_stdout = sys.stdout
        sys.stdout = buf
        try:
            objgraph.show_growth(limit=15)
        finally:
            sys.stdout = original_stdout
        growth_info = buf.getvalue().strip()

    if growth_info:
        logger.debug("--- Leaks Check (%s) ---\n%s", stage, growth_info)

    vs_types = ["Core", "VideoNode", "AudioNode", "VideoFrame", "AudioFrame"]

    for type_name in vs_types:
        objs = objgraph.by_type(type_name)
        if not objs:
            continue

        logger.debug("Lingering %s objects: %d", type_name, len(objs))

        if stage == "after" and type_name in ["Core", "VideoNode"]:
            try:
                filename = f"leak_{type_name.lower()}_{stage.replace(' ', '_').lower()}.dot"
                objgraph.show_backrefs(
                    objs[:1],
                    max_depth=10,
                    filename=filename,
                    highlight=lambda x, this_objs=objs: x in this_objs,
                )
                logger.warning("Potential %s leak! Backref graph saved to %s", type_name, filename)
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not generate leak graph for %s: %s", type_name, e)

    # Check for QObject leaks
    qobjects = [obj for obj in gc.get_objects() if isinstance(obj, QObject)]
    lingering_qobjects = []

    for obj in qobjects:
        class_name = obj.__class__.__name__
        if (
            "Workspace" in class_name
            or "Plugin" in class_name
            or class_name in ("TabViewWidget", "Timeline", "PlaybackContainer", "GraphicsView")
        ):
            lingering_qobjects.append(obj)

    if lingering_qobjects:
        logger.debug("--- Lingering QObjects Check (%s) ---", stage)
        for obj in lingering_qobjects:
            is_valid = Shiboken.isValid(obj)
            logger.warning(
                "Lingering QObject: %s at %s (C++ Valid: %s)",
                obj.__class__.__name__,
                hex(id(obj)),
                is_valid,
            )

            # If running the check after deletion, generate backref graph for lingering instances
            if stage == "after" and is_valid:
                try:
                    filename = f"leak_qobject_{obj.__class__.__name__.lower()}_{stage.replace(' ', '_').lower()}.dot"
                    objgraph.show_backrefs(
                        [obj],
                        max_depth=10,
                        filename=filename,
                        highlight=lambda x, target=obj: x is target,
                    )
                    logger.warning("Potential QObject leak! Backref graph saved to %s", filename)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Could not generate QObject leak graph for %s: %s", obj.__class__.__name__, e)


class LRUCache[K, V](OrderedDict[K, V]):
    def __init__(self, cache_size: int = 10) -> None:
        super().__init__()
        self.cache_size = cache_size

    @override
    def __getitem__(self, key: K) -> V:
        val = super().__getitem__(key)
        super().move_to_end(key)

        return val

    @override
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
        self.lock = threading.Lock()

        vs.register_on_destroy(self.clear)

    @override
    def __setitem__(self, key: int, value: vs.VideoFrame) -> None:
        with self.lock:
            super().__setitem__(key, value)

            if len(self) > self.cache_size:
                del self[next(iter(self.data.keys()))]

    @override
    def __getitem__(self, key: int) -> vs.VideoFrame:
        with self.lock:
            in_cache = key in self

        if not in_cache and (c := self.clip()):
            return self.add_frame(key, c.get_frame(key))

        with self.lock:
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


if TYPE_CHECKING:

    class ObjectType(type):
        """Metaclass type of any Shiboken.Object."""
else:
    ObjectType = type(Shiboken.Object)
    """Metaclass type of any Shiboken.Object."""


class QObjectSet[T: QObject](MutableSet[T]):
    """
    A `WeakSet` for QObjects that also hooks the `destroyed` signal for C++ deletion.

    Entries are removed both when the Python wrapper is garbage-collected (via `WeakSet`)
    and when the C++ object is destroyed by Qt (via `destroyed`).
    """

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data = weakref.WeakSet[T]()

    @override
    def __contains__(self, value: object) -> bool:
        return value in self._data

    @override
    def __iter__(self) -> Iterator[T]:
        return iter(self._data)

    @override
    def __len__(self) -> int:
        return len(self._data)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    @override
    def add(self, value: T) -> None:
        if value not in self._data:
            self._data.add(value)
            value.destroyed.connect(lambda: self.discard(value))

    @override
    def discard(self, value: T) -> None:
        self._data.discard(value)


class QObjectCounter[T: QObject](Container[T], Sized):
    """
    Refcount QObjects while cleaning up entries when Qt destroys the object.
    """

    __slots__ = ("_cleanup", "_counts", "_lock")

    def __init__(self) -> None:
        self._counts = weakref.WeakKeyDictionary[T, int]()
        self._cleanup = weakref.WeakKeyDictionary[T, Callable[..., None]]()
        self._lock = threading.RLock()

    @override
    def __contains__(self, value: object) -> bool:
        with self._lock:
            return value in self._counts

    @override
    def __len__(self) -> int:
        with self._lock:
            return len(self._counts)

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._counts)

    def count(self, value: T) -> int:
        with self._lock:
            return self._counts.get(value, 0)

    def add(self, value: T) -> int:
        with self._lock:
            if value in self._counts:
                self._counts[value] += 1
                return self._counts[value]

            ref = weakref.ref(value)

            def cleanup(*_: object) -> None:
                with self._lock:
                    if obj := ref():
                        self._counts.pop(obj, None)
                        self._cleanup.pop(obj, None)

            self._counts[value] = 1
            self._cleanup[value] = cleanup
            value.destroyed.connect(cleanup)

            return 1

    def discard(self, value: T) -> int:
        with self._lock:
            count = self._counts.get(value)

            if count is None:
                return 0

            if count <= 1:
                self._counts.pop(value, None)
                self._cleanup.pop(value, None)
                return 0

            count -= 1
            self._counts[value] = count

            return count
