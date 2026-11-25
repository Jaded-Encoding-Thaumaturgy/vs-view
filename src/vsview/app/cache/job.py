from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractContextManager
from enum import Enum, auto
from logging import getLogger
from typing import NamedTuple

import vapoursynth as vs
from jetpytools import clamp

from .exceptions import RenderCacheError
from .node import OutputCache
from .storage import DiskFrameStore

logger = getLogger(__name__)


class JobState(Enum):
    RUNNING = auto()
    PAUSED = auto()  # Paused by the user
    WAITING = auto()  # Not started yet, or paused automatically (playback, another output is shown)
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


ACTIVE_STATES = frozenset({JobState.RUNNING, JobState.PAUSED, JobState.WAITING})


class JobProgress(NamedTuple):
    state: JobState
    fps: float
    """Frames written per second over the last frames, or 0 when not known yet."""
    failed_frame: int | None
    error: str | None


class FrameRequest(NamedTuple):
    n: int
    frame: Future[vs.VideoFrame]


class RenderJob:
    """Renders a frame range of one output into its disk store."""

    FPS_WINDOW = 16
    MIN_CONCURRENCY = 1
    MAX_CONCURRENCY = 16

    def __init__(
        self,
        cache: OutputCache,
        store: DiskFrameStore,
        vs_context: Callable[[], AbstractContextManager[None, bool | None]],
        frames: range,
        concurrency: int,
    ) -> None:
        self.cache = cache
        self.store = store
        self.vs_context = vs_context
        self.frames = frames
        self.concurrency = clamp(concurrency, RenderJob.MIN_CONCURRENCY, RenderJob.MAX_CONCURRENCY)

        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._frames = iter(frames)
        self._requests = deque[FrameRequest]()
        self._writer: threading.Thread | None = None
        self._write_times = deque[float](maxlen=self.FPS_WINDOW)

        self._in_flight = 0
        self._exhausted = False
        self._started = False
        self._user_paused = False
        self._waiting = False
        self._cancelled = False
        self._failed_frame: int | None = None
        self._error: str | None = None

    @property
    def progress(self) -> JobProgress:
        with self._lock:
            times = self._write_times
            fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0

            return JobProgress(self._state(), fps, self._failed_frame, self._error)

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._refill()

    def pause(self) -> None:
        with self._lock:
            self._user_paused = True

    def resume(self) -> None:
        with self._lock:
            self._user_paused = False
            self._write_times.clear()
            self._refill()

    def set_concurrency(self, concurrency: int) -> None:
        with self._lock:
            self.concurrency = max(1, concurrency)
            self._refill()

    def set_waiting(self, waiting: bool) -> None:
        """Pause or resume the job automatically. This never ends a pause by the user."""
        with self._lock:
            self._waiting = waiting
            self._write_times.clear()
            self._refill()

    def cancel(self, timeout: float = 10.0) -> bool:
        """
        Stop requesting frames, then wait for the frames in flight. They are closed without being written.

        Returns:
            False if frames were still in flight after `timeout` seconds.
        """
        with self._lock:
            self._cancelled = True
            return self._idle.wait_for(lambda: self._in_flight == 0, timeout)

    def wait(self, timeout: float | None = None) -> bool:
        """
        Wait until no frame is in flight: the job is done, failed, cancelled, or paused.

        Returns:
            False on timeout.
        """
        with self._lock:
            return self._idle.wait_for(lambda: self._in_flight == 0 or not self._started, timeout)

    def _state(self) -> JobState:
        if self._cancelled:
            return JobState.CANCELLED
        if self._error is not None:
            return JobState.FAILED
        if self._exhausted and self._in_flight == 0:
            return JobState.DONE
        if self._user_paused:
            return JobState.PAUSED
        if self._waiting or not self._started:
            return JobState.WAITING
        return JobState.RUNNING

    def _refill(self) -> None:
        if (
            not self._started
            or self._user_paused
            or self._waiting
            or self._cancelled
            or self._error is not None
            or self._exhausted
        ):
            return

        while self._in_flight < self.concurrency:
            if (n := next((n for n in self._frames if n not in self.store), None)) is None:
                self._exhausted = True
                self._idle.notify_all()
                break

            try:
                with self.vs_context():
                    request = FrameRequest(n, self.cache.source.get_frame_async(n))
            except Exception as e:  # noqa: BLE001
                self._fail(n, e)
                break

            self._requests.append(request)
            self._in_flight += 1

        if self._requests and self._writer is None:
            self._writer = threading.Thread(target=self._write_loop, name="RenderCacheWriter", daemon=True)
            self._writer.start()

    def _write_loop(self) -> None:
        while True:
            with self._lock:
                if not self._requests:
                    self._writer = None
                    return
                request = self._requests.popleft()

            error: Exception | None = None
            try:
                frame = request.frame.result()
            except vs.Error as e:
                error = e
            else:
                with self._lock:
                    cancelled = self._cancelled
                if not cancelled:
                    try:
                        self.store.write(request.n, frame)
                    except (ValueError, RenderCacheError) as e:
                        error = e
                    else:
                        with self._lock:
                            self._write_times.append(time.perf_counter())
                    finally:
                        frame.close()

            with self._lock:
                self._in_flight -= 1
                if error is not None:
                    self._fail(request.n, error)
                self._refill()
                self._idle.notify_all()

    def _fail(self, n: int, error: BaseException) -> None:
        if self._error is not None or self._cancelled:
            return
        self._failed_frame = n
        self._error = str(error) or type(error).__name__
        logger.error("The render cache stopped at frame %d: %s", n, self._error)
        logger.debug("Full traceback:", exc_info=error)
