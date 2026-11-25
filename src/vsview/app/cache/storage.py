from __future__ import annotations

import ctypes
import io
import marshal
import os
import shutil
import struct
from collections.abc import Container, Mapping
from contextlib import suppress
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import Any, NamedTuple, override
from uuid import uuid4

import vapoursynth as vs
from jetpytools import clamp
from platformdirs import user_cache_path

from .exceptions import CacheFrameError, QuotaExceededError, RenderCacheError
from .io import LockPath

logger = getLogger(__name__)
core = vs.core

MAGIC = b"VSRC"
VERSION = 1
# magic, version, props length
HEADER = struct.Struct("<4sHI")
# stride of one plane
SEGMENT = struct.Struct("<I")
RESERVED_BYTES = 1 << 30  # 1 GiB
# The folder created inside a cache directory that the user picks.
CUSTOM_ROOT_NAME = "vsview_render_cache"


def default_root() -> Path:
    return user_cache_path("vsview", appauthor=False) / "render_cache"


class Spec(NamedTuple):
    width: int
    height: int
    format: vs._VideoFormatDict


def estimate_frame_size(clip: vs.VideoNode) -> int:
    """Size of one frame file of a constant-format clip, without stride padding and props."""
    size = HEADER.size
    fmt = clip.format
    heights = _get_plane_heights(clip.height, fmt.subsampling_h, fmt.num_planes)

    for p, height in enumerate(heights):
        width = clip.width >> fmt.subsampling_w if p > 0 else clip.width
        size += SEGMENT.size + width * fmt.bytes_per_sample * height

    return size


class CacheSession:
    """
    The render cache of one app process.

    It owns the session directory, the lock that marks the session as alive, and the disk quota shared by all stores.
    """

    def __init__(self, root: Path, quota_bytes: int) -> None:
        root.mkdir(parents=True, exist_ok=True)
        name = f"{os.getpid()}-{uuid4().hex[:8]}"

        self._lock_path = LockPath(root / f"{name}.lock")

        if not self._lock_path.acquire():
            raise RenderCacheError(f"Can't lock the render cache session file {self._lock_path.path}")

        self.path = root / name
        self.path.mkdir()
        self.quota_bytes = quota_bytes

        self._used = 0
        self._lock = Lock()
        self._closed = False

    @property
    def used_bytes(self) -> int:
        with self._lock:
            return self._used

    @property
    def available_bytes(self) -> int:
        free = max(0, shutil.disk_usage(self.path).free - RESERVED_BYTES)

        with self._lock:
            return clamp(self.quota_bytes - self._used, 0, free)

    def create_store(self, clip: vs.VideoNode) -> DiskFrameStore:
        path = self.path / uuid4().hex
        path.mkdir()
        return DiskFrameStore(self, path, clip)

    def reserve(self, size: int) -> None:
        """
        Raises:
            QuotaExceededError: `size` more bytes would go over the quota.
        """
        with self._lock:
            if self._used + size > self.quota_bytes:
                raise QuotaExceededError("The render cache disk quota is reached")
            self._used += size

    def release(self, size: int) -> None:
        with self._lock:
            self._used -= size

    def close(self) -> None:
        """Delete the session directory and release the lock. All stores of the session become unusable."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._used = 0

        shutil.rmtree(self.path, ignore_errors=True)
        self._lock_path.release()


class DiskFrameStore(Container[int]):
    """The cached frames of one video output. The clip must have a constant format and resolution."""

    FILE_SUFFIX = ".vsc"

    def __init__(self, session: CacheSession, path: Path, clip: vs.VideoNode) -> None:
        self.session = session
        self.path = path
        self.spec = Spec(clip.width, clip.height, clip.format._as_dict())

        self._index = dict[int, int]()
        self._writing = set[int]()
        self._size = 0
        self._lock = Lock()
        self._closed = False
        self._dropped_props = set[str]()

    @override
    def __contains__(self, n: object) -> bool:
        with self._lock:
            return n in self._index

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._index)

    def frames(self) -> list[int]:
        with self._lock:
            return sorted(self._index)

    def count_in(self, frames: range) -> int:
        """Return how many frames of `frames` are cached."""
        with self._lock:
            return sum(n in frames for n in self._index)

    def runs(self) -> list[tuple[int, int]]:
        """Return the cached frames as sorted, inclusive `(first, last)` runs of consecutive frames."""
        runs = list[tuple[int, int]]()

        for n in self.frames():
            if runs and runs[-1][1] == n - 1:
                runs[-1] = (runs[-1][0], n)
            else:
                runs.append((n, n))

        return runs

    def write(self, n: int, frame: vs.VideoFrame) -> None:
        """
        Save frame `n` with its props, and add it to the index.

        A frame that is already cached, or that another thread is writing, is not written again.

        Raises:
            ValueError: The frame doesn't match the format and size of the store.
            QuotaExceededError: The file would go over the session quota. Nothing is written.
            RenderCacheError: The store is closed.
        """
        with self._lock:
            if self._closed:
                raise RenderCacheError("The render cache store is closed")
            # A cancelled job can still be writing a frame that the job started after it renders too.
            if n in self._index or n in self._writing:
                return
            self._writing.add(n)

        try:
            self._write_file(n, frame)
        finally:
            with self._lock:
                self._writing.discard(n)

    def read(self, n: int) -> vs.VideoFrame:
        """
        Return frame `n` with its props.

        Raises:
            CacheFrameError: The frame isn't cached, or its file is missing or invalid.
                A frame that can't be read is removed from the index.
        """
        if n not in self:
            raise CacheFrameError(f"Frame {n} is not cached")

        frame = core.create_video_frame(self.spec.format["id"], self.spec.width, self.spec.height)

        try:
            if not isinstance(props := marshal.loads(self._read_planes(n, frame)), dict):
                raise CacheFrameError("the props aren't a dict")
            frame.props.update(props)
        except (OSError, ValueError, EOFError, TypeError, CacheFrameError) as e:
            frame.close()
            self._drop(n)
            raise CacheFrameError(f"Frame {n} can't be read from the render cache: {e}") from e

        return frame

    def close(self) -> None:
        """Delete all cached frames and the store directory. Nothing can be written after this."""
        with self._lock:
            self._closed = True
            size, self._size = self._size, 0
            self._index.clear()

        self.session.release(size)
        shutil.rmtree(self.path, ignore_errors=True)

    def _write_file(self, n: int, frame: vs.VideoFrame) -> None:
        if Spec(frame.width, frame.height, frame.format._as_dict()) != self.spec:
            raise ValueError(f"The frame doesn't match the cached format and size: {frame}")

        table = bytearray()
        planes = list[memoryview]()

        fmt = frame.format
        heights = _get_plane_heights(frame.height, fmt.subsampling_h, fmt.num_planes)

        for p, height in enumerate(heights):
            stride = frame.get_stride(p)
            table += SEGMENT.pack(stride)
            planes.append(_plane_buffer(frame.get_read_ptr(p), stride * height))

        props = marshal.dumps(self._storable_props(frame.props))
        header = HEADER.pack(MAGIC, VERSION, len(props))
        size = len(header) + len(table) + len(props) + sum(plane.nbytes for plane in planes)

        self.session.reserve(size)
        path = self._frame_path(n)

        try:
            with path.open("wb") as fh:
                fh.writelines([header, table, props, *planes])
        except BaseException:
            self._discard(path, size)
            raise

        with self._lock:
            if not self._closed:
                self._index[n] = size
                self._size += size
                return

        self._discard(path, size)

    def _read_planes(self, n: int, dst: vs.VideoFrame) -> bytearray:
        fmt = self.spec.format
        heights = _get_plane_heights(self.spec.height, fmt["subsampling_h"], fmt["num_planes"])

        with self._frame_path(n).open("rb", buffering=0) as fh:
            magic, version, props_len = HEADER.unpack(_read_exact(fh, HEADER.size))

            if magic != MAGIC or version != VERSION:
                raise CacheFrameError("unknown file format")

            strides = [stride for (stride,) in SEGMENT.iter_unpack(_read_exact(fh, SEGMENT.size * len(heights)))]
            props_blob = _read_exact(fh, props_len)

            for p, (height, stride) in enumerate(zip(heights, strides, strict=True)):
                # The stride only depends on the format and the width, so it matches in the process that wrote it.
                if stride != (dst_stride := dst.get_stride(p)):
                    raise CacheFrameError(f"plane {p} has a stride of {stride}, expected {dst_stride}")

                _readinto_exact(fh, _plane_buffer(dst.get_write_ptr(p), stride * height))

        return props_blob

    def _storable_props(self, props: Mapping[str, Any]) -> dict[str, Any]:
        kept = dict[str, Any]()
        dropped = set[str]()

        for key, value in props.items():
            if isinstance(value, list):
                plain = [_plain_prop_value(v) for v in value]
                stored: Any = None if None in plain else plain
            else:
                stored = _plain_prop_value(value)

            if stored is None:
                dropped.add(key)
            else:
                kept[key] = stored

        if new := dropped - self._dropped_props:
            self._dropped_props |= new
            logger.warning(
                "The render cache can't store these frame props. Frames read from the cache won't have them: %s",
                ", ".join(sorted(new)),
            )

        return kept

    def _drop(self, n: int) -> None:
        with self._lock:
            size = self._index.pop(n, 0)
            self._size -= size

        self._discard(self._frame_path(n), size)

    def _discard(self, path: Path, size: int) -> None:
        self.session.release(size)
        with suppress(OSError):
            path.unlink(missing_ok=True)

    def _frame_path(self, n: int) -> Path:
        return self.path / f"{n}{self.FILE_SUFFIX}"


def _get_plane_heights(height: int, subsampling_h: int, num_planes: int) -> list[int]:
    return [height >> subsampling_h if p > 0 else height for p in range(num_planes)]


def _plain_prop_value(value: Any) -> int | float | str | bytes | None:
    """Return `value` as its exact built-in type, or None when a frame file can't store it."""
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        # `str()` would give "Class.MEMBER" for a str-based enum.
        return str.__str__(value)
    if isinstance(value, bytes):
        return bytes(value)
    return None


def _plane_buffer(ptr: ctypes.c_void_p, size: int) -> memoryview:
    if (address := ptr.value) is None:
        raise RenderCacheError("The frame plane pointer is NULL")

    return memoryview((ctypes.c_char * size).from_address(address)).cast("B")


def _readinto_exact(fh: io.FileIO, target: memoryview) -> None:
    done = 0

    while done < target.nbytes:
        if not (count := fh.readinto(target[done:])):
            raise EOFError(f"the file ends after {done} of {target.nbytes} bytes")
        done += count


def _read_exact(fh: io.FileIO, size: int) -> bytearray:
    data = bytearray(size)
    _readinto_exact(fh, memoryview(data))
    return data
