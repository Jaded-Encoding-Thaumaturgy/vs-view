"""Utility functions for vsview."""

import gc
import hashlib
import io
import os
import sys
import threading
import weakref
from collections import OrderedDict, UserDict
from collections.abc import Callable, Container, Generator, ItemsView, Iterator, KeysView, MutableSet, Sized, ValuesView
from contextlib import contextmanager
from dataclasses import dataclass
from logging import DEBUG, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self, override

import vapoursynth as vs
from jetpytools import inject_self
from PySide6.QtCore import QObject, QThread, QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import Shiboken

logger = getLogger(__name__)


@dataclass(slots=True)
class ProcessMemorySnapshot:
    rss: int
    """Resident Set Size / Working Set (physical memory currently paged in)."""
    vms: int
    """Virtual Memory Size."""
    private: int
    """Private Bytes / Commit Size (dedicated virtual memory, unavailable to other processes)."""
    uss: int | None
    """Unique Set Size (physical memory unique to this process, excluding shared pages)."""
    python_traced: int | None
    """Python heap memory tracked by tracemalloc if enabled, in bytes."""

    def __init__(self) -> None:
        self.rss = 0
        self.vms = 0
        self.private = 0
        self.uss = None
        self.python_traced = None

        try:
            import tracemalloc

            import psutil
        except ImportError:
            logger.exception("")
            return

        proc = psutil.Process()
        mem_info = proc.memory_info()

        self.rss = mem_info.rss
        self.vms = mem_info.vms
        # On Windows, mem_info.private holds Commit Size / Private Bytes.
        # Fall back to RSS on platforms without private bytes.
        self.private = getattr(mem_info, "private", mem_info.rss)

        try:
            full_info = proc.memory_full_info()
            self.uss = getattr(full_info, "uss", None)
        except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
            self.uss = None

        self.python_traced = tracemalloc.get_traced_memory()[0] if tracemalloc.is_tracing() else None

    @override
    def __str__(self) -> str:
        return self.format()

    @property
    def rss_mb(self) -> float:
        """Working set / RSS in MiB."""
        return self.rss / (1024 * 1024)

    @property
    def vms_mb(self) -> float:
        """Virtual size in MiB."""
        return self.vms / (1024 * 1024)

    @property
    def private_mb(self) -> float:
        """Commit size / Private bytes in MiB."""
        return self.private / (1024 * 1024)

    @property
    def uss_mb(self) -> float | None:
        """Unique set size in MiB."""
        return self.uss / (1024 * 1024) if self.uss is not None else None

    @property
    def python_traced_mb(self) -> float | None:
        """Python traced heap in MiB."""
        return self.python_traced / (1024 * 1024) if self.python_traced is not None else None

    @inject_self
    def format(self, before: Self | None = None) -> str:
        """
        Format memory usage, optionally comparing against a previous snapshot.

        Args:
            before: Previous snapshot to compute and display deltas against.
        """

        def fmt(label: str, curr: float | None, prev: float | None) -> str | None:
            if curr is None:
                return None

            return (
                f"{label}: {curr:.2f} MiB ({curr - prev:+0.2f} MiB)" if prev is not None else f"{label}: {curr:.2f} MiB"
            )

        parts = [
            fmt("Commit/Private", self.private_mb, before.private_mb if before else None),
            fmt("WorkingSet/RSS", self.rss_mb, before.rss_mb if before else None),
            fmt("USS", self.uss_mb, before.uss_mb if before else None),
            fmt("PyHeap", self.python_traced_mb, before.python_traced_mb if before else None),
        ]

        return ", ".join(p for p in parts if p is not None)


@contextmanager
def measure_memory(
    label: str = "Memory", force_gc: bool = True, log_level: int = DEBUG
) -> Generator[ProcessMemorySnapshot | None, None, None]:
    """
    Context manager to measure and log process memory before and after an operation.

    Args:
        label: Label to prepend to log messages.
        force_gc: Whether to run gc.collect() before taking each snapshot.
        log_level: Logging level (defaults to logging.DEBUG / 10).
    """
    if force_gc:
        gc.collect()

    before = ProcessMemorySnapshot()
    logger.log(log_level, "[%s] Memory (before) -> %s", label, before.format())

    try:
        yield before
    finally:
        if force_gc:
            gc.collect()

        after = ProcessMemorySnapshot()
        logger.log(log_level, "[%s] Memory (after) -> %s", label, after.format(before))


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


class _CheckLeaks:
    def __init__(self, func: Callable[..., Any]) -> None:
        self.func = func
        self._snapshot_before: ProcessMemorySnapshot | None = None

    def __call__(self, stage: Literal["before", "after"]) -> None:
        before = self._snapshot_before if stage == "after" else None
        if stage == "before":
            self._snapshot_before = ProcessMemorySnapshot()
        elif stage == "after":
            self._snapshot_before = None
        return self.func(stage, before=before)

    @contextmanager
    def ctx(self) -> Generator[None]:
        from ..env import getenv_bool

        if not getenv_bool("VSVIEW_DEBUG"):
            yield
            return

        is_gui_thread = (app := QApplication.instance()) and QThread.currentThread() == app.thread()

        if is_gui_thread:
            QTimer.singleShot(0, lambda: self("before"))
        else:
            self("before")

        try:
            yield
        finally:
            if is_gui_thread:
                QTimer.singleShot(0, lambda: self("after"))
            else:
                self("after")


@_CheckLeaks
def check_leaks(stage: Literal["before", "after"], *, before: ProcessMemorySnapshot | None = None) -> None:
    gc.collect()

    mem = ProcessMemorySnapshot()
    logger.debug("--- Memory Snapshot (%s) --- %s", stage, mem.format(before if stage == "after" else None))

    try:
        import objgraph  # type: ignore[import-untyped]
    except ImportError:
        logger.exception("")
        return

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


class LRUCache[K, V](UserDict[K, V]):
    data: OrderedDict[K, V]

    def __init__(self, cache_size: int = 10) -> None:
        super().__init__()
        self.data = OrderedDict[K, V]()  # pyright: ignore[reportIncompatibleVariableOverride]
        self.cache_size = cache_size
        self.lock = threading.RLock()

    @override
    def __getitem__(self, key: K) -> V:
        with self.lock:
            val = self.data[key]
            self.data.move_to_end(key)
            return val

    @override
    def __setitem__(self, key: K, value: V) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.cache_size:
                self.data.popitem(last=False)

    @override
    def __delitem__(self, key: K) -> None:
        with self.lock:
            del self.data[key]

    @override
    def __iter__(self) -> Iterator[K]:
        with self.lock:
            return iter(self.data.copy())

    def __reversed__(self) -> Iterator[K]:
        with self.lock:
            return reversed(self.data.copy())

    @override
    def keys(self) -> KeysView[K]:
        with self.lock:
            return self.data.copy().keys()

    @override
    def values(self) -> ValuesView[V]:
        with self.lock:
            return self.data.copy().values()

    @override
    def items(self) -> ItemsView[K, V]:
        with self.lock:
            return self.data.copy().items()

    @override
    def clear(self) -> None:
        with self.lock:
            self.data.clear()


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
