from __future__ import annotations

from collections.abc import Callable, Mapping
from logging import getLogger
from typing import TYPE_CHECKING

from PySide6.QtCore import QLocale, QObject, QTimer, Signal, Slot
from vsengine import ManagedEnvironment, UnifiedFuture

from ...vsenv import run_in_loop
from ..cache.exceptions import RenderCacheError
from ..cache.job import ACTIVE_STATES, JobState, RenderJob
from ..cache.session import get_session
from ..cache.storage import DiskFrameStore, estimate_frame_size
from ..settings import SettingsManager
from ..settings.models import CacheRange
from ..views import Notification

if TYPE_CHECKING:
    from ...vsenv import QtEventLoop
    from ..outputs import OutputsManager, VideoOutput

logger = getLogger(__name__)


class RenderCacheController(QObject):
    NOTIFICATION_TITLE = "Render Cache"
    NOTIFICATION_KEY = "vsview.render_cache.key"
    REFRESH_INTERVAL_MS = 250
    RELEASE_TIMEOUT = 10.0

    changed = Signal()
    notificationRequested = Signal(object)

    def __init__(
        self,
        loop: QtEventLoop,
        get_env: Callable[[], ManagedEnvironment],
        outputs_manager: OutputsManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._loop = loop
        self._get_env = get_env
        self._outputs_manager = outputs_manager

        self._ranges = dict[int, CacheRange]()  # Keyed by `vs_index`
        self._jobs = dict[int, RenderJob]()
        self._retired = list[RenderJob]()  # Replaced or cleared while frames may still be in flight.
        self._reported = dict[int, RenderJob]()
        self._shown: tuple[object, ...] | None = None
        self._playing = False
        self._concurrency_override: int | None = None

        self._timer = QTimer(self, interval=self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    @property
    def ranges(self) -> dict[int, CacheRange]:
        return {vs_index: r.model_copy() for vs_index, r in self._ranges.items()}

    @property
    def concurrency_override(self) -> int | None:
        return self._concurrency_override

    @property
    def concurrency(self) -> int:
        return (
            self._concurrency_override
            if self._concurrency_override is not None
            else SettingsManager.global_settings.render_cache.concurrency
        )

    def set_ranges(self, ranges: Mapping[int, CacheRange]) -> None:
        self._ranges = {vs_index: r.model_copy(update={"started": False}) for vs_index, r in ranges.items()}

    def set_concurrency_override(self, concurrency: int | None) -> None:
        self._concurrency_override = concurrency

        for job in self._jobs.values():
            job.set_concurrency(self.concurrency)

    def job(self, vs_index: int) -> RenderJob | None:
        return self._jobs.get(vs_index)

    def unavailable_reason(self) -> str | None:
        if (voutput := self._outputs_manager.current_voutput) is None:
            return "No output is loaded"
        if voutput.cache is not None:
            return None
        return voutput.cache_unavailable_reason or "The render cache is disabled in the settings"

    def current_range(self) -> CacheRange | None:
        """A copy of the range of the current output, when it can be cached and has one."""
        voutput = self._outputs_manager.current_voutput

        if voutput is None or voutput.cache is None or (r := self._ranges.get(voutput.vs_index)) is None:
            return None

        return r.model_copy()

    def render_state(self) -> JobState | None:
        """The state of the render of the current output, or None when it has no render."""
        voutput = self._outputs_manager.current_voutput
        job = self._jobs.get(voutput.vs_index) if voutput is not None else None
        return job.progress.state if job is not None else None

    def cached_runs(self) -> list[tuple[int, int]]:
        """The cached frames of the current output, as sorted, inclusive `(first, last)` runs."""
        return store.runs() if (store := self._current_store) is not None else []

    def range_label(self) -> str:
        voutput = self._outputs_manager.current_voutput

        if (
            voutput is None
            or (cache := voutput.cache) is None
            or (r := self._ranges.get(voutput.vs_index)) is None
            or (frames := r.frames) is None
        ):
            return ""

        store = cache.store
        done = store.count_in(frames) if store is not None else 0
        total = len(frames)

        job = self._jobs.get(voutput.vs_index)
        progress = job.progress if job is not None and job.frames == frames else None
        state = progress.state if progress is not None else None

        match state:
            case JobState.PAUSED:
                title = "Cache (paused)"
            case JobState.WAITING:
                title = "Cache (waiting)"
            case JobState.FAILED if progress is not None:
                title = f"Cache (failed at {progress.failed_frame})"
            case _:
                title = "Cache"

        parts = [title, f"{total} f" if done in (0, total) else f"{done}/{total} f"]

        if done == 0:
            parts.append(f"est. {format_size(total * estimate_frame_size(cache.source))}")
        elif store is not None:
            parts.append(format_size(store.size_bytes))

        if state is JobState.RUNNING and progress is not None and progress.fps > 0:
            parts.append(f"{progress.fps:.1f} fps")
            parts.append(f"ETA {format_duration((total - done) / progress.fps)}")

        return " · ".join(parts)

    def set_start(self, frame: int) -> None:
        if (voutput := self._current_cacheable) is None:
            return

        r = self._ranges.setdefault(voutput.vs_index, CacheRange())

        if r.start == frame:
            return

        if r.end is not None and frame > r.end:
            r.end = None

        r.start = frame
        self._on_range_changed(voutput)

    def set_end(self, frame: int) -> None:
        if (voutput := self._current_cacheable) is None:
            return

        r = self._ranges.setdefault(voutput.vs_index, CacheRange())

        if r.end == frame:
            return

        if r.start is not None and frame < r.start:
            r.start = None

        r.end = frame
        self._on_range_changed(voutput)

    @Slot()
    def toggle_render(self) -> None:
        if (voutput := self._current_cacheable) is None:
            return

        job = self._jobs.get(voutput.vs_index)
        state = job.progress.state if job is not None else None

        if job is not None and state is JobState.PAUSED:
            job.resume()
        elif job is not None and state in (JobState.RUNNING, JobState.WAITING):
            job.pause()
        else:
            self._start_job(voutput)

        self._timer.start()
        self._emit_changed()

    @Slot()
    def clear(self) -> None:
        if (voutput := self._outputs_manager.current_voutput) is None:
            return

        self._retire(voutput.vs_index)
        self._ranges.pop(voutput.vs_index, None)

        # Detaching stops the reads at once. The next render creates a new store.
        if voutput.cache is not None and (store := voutput.cache.detach_store()) is not None:
            self._loop.to_thread_named("RenderCacheClear", store.close)

        self._emit_changed()

    @Slot(bool)
    def set_playing(self, playing: bool) -> None:
        """Make every render wait while something plays."""
        self._playing = playing
        self._update_waiting()
        self._emit_changed()

    @Slot()
    def on_current_output_changed(self) -> None:
        self._update_waiting()
        self._emit_changed()

    def on_outputs_ready(self, *, is_reload: bool) -> None:
        """Fit the ranges to the new outputs, and restart the renders after a reload when the settings ask for it."""
        voutputs = {voutput.vs_index: voutput for voutput in self._outputs_manager.voutputs}

        for vs_index, r in list(self._ranges.items()):
            if (voutput := voutputs.get(vs_index)) is None or voutput.cache is None:
                del self._ranges[vs_index]
                continue

            last = voutput.vs_output.clip.num_frames - 1
            r.start = min(r.start, last) if r.start is not None else None
            r.end = min(r.end, last) if r.end is not None else None

        if is_reload and SettingsManager.global_settings.render_cache.render_after_reload:
            for vs_index, r in self._ranges.items():
                if r.started:
                    self._start_job(voutputs[vs_index])

        self._emit_changed()

    @run_in_loop(return_future=False)
    def release(self) -> UnifiedFuture[None]:
        """Cancel every render, and stop reading from the stores. They are deleted in the background."""
        self._timer.stop()

        jobs = [*self._jobs.values(), *self._retired]
        self._jobs.clear()
        self._retired.clear()
        self._reported.clear()

        for job in jobs:
            job.cancel(timeout=0)

        stores = [
            store
            for voutput in self._outputs_manager.voutputs
            if voutput.cache is not None and (store := voutput.cache.detach_store()) is not None
        ]

        if stores:
            self._loop.to_thread_named("RenderCacheCleanup", _close_stores, stores)

        self._emit_changed()

        if not jobs:
            return UnifiedFuture.resolve(None)

        def wait_for_jobs() -> None:
            timeout = self.RELEASE_TIMEOUT
            for job in jobs:
                if not job.wait(timeout):
                    logger.warning("A frame of a cancelled render cache job is still in flight after %.0f s", timeout)

        return self._loop.to_thread_named("RenderCacheRelease", wait_for_jobs)

    @property
    def _current_store(self) -> DiskFrameStore | None:
        voutput = self._outputs_manager.current_voutput
        return voutput.cache.store if voutput is not None and voutput.cache is not None else None

    @property
    def _current_cacheable(self) -> VideoOutput | None:
        if (reason := self.unavailable_reason()) is not None:
            self.notificationRequested.emit(
                Notification(reason, Notification.Severity.ERROR, self.NOTIFICATION_TITLE, self.NOTIFICATION_KEY)
            )
            return None

        return self._outputs_manager.current_voutput

    def _should_wait(self, vs_index: int) -> bool:
        current = self._outputs_manager.current_voutput
        return self._playing or current is None or vs_index != current.vs_index

    def _update_waiting(self) -> None:
        for vs_index, job in self._jobs.items():
            job.set_waiting(self._should_wait(vs_index))

    def _retire(self, vs_index: int) -> None:
        if (job := self._jobs.pop(vs_index, None)) is not None:
            job.cancel(timeout=0)
            self._retired.append(job)

    def _on_range_changed(self, voutput: VideoOutput) -> None:
        job = self._jobs.get(voutput.vs_index)

        # A render of the old range goes on with the new one, in the same pause state.
        if job is not None and (state := job.progress.state) in ACTIVE_STATES:
            self._retire(voutput.vs_index)
            self._start_job(voutput, paused=state is JobState.PAUSED)

        self._emit_changed()

    def _start_job(self, voutput: VideoOutput, *, paused: bool = False) -> None:
        vs_index = voutput.vs_index
        r = self._ranges.get(vs_index)

        if r is None or (frames := r.frames) is None or (cache := voutput.cache) is None:
            return

        try:
            session = get_session()
        except (OSError, RenderCacheError) as e:
            logger.error("The render cache can't start: %s", e)
            self.notificationRequested.emit(
                Notification(
                    f"The render cache can't start: {e}",
                    Notification.Severity.ERROR,
                    self.NOTIFICATION_TITLE,
                    self.NOTIFICATION_KEY,
                )
            )
            return

        store = cache.ensure_store(session)
        needed = (len(frames) - store.count_in(frames)) * estimate_frame_size(cache.source)

        if needed > (available := session.available_bytes):
            self.notificationRequested.emit(
                Notification(
                    f"Not enough space for the render cache: {format_size(needed)} needed, "
                    f"{format_size(available)} available",
                    Notification.Severity.ERROR,
                    self.NOTIFICATION_TITLE,
                    self.NOTIFICATION_KEY,
                )
            )
            return

        # The job it replaces can still have frames in flight, even after it failed.
        self._retire(vs_index)

        job = RenderJob(cache, store, self._get_env().use, frames, self.concurrency)

        if paused:
            job.pause()

        job.set_waiting(self._should_wait(vs_index))
        job.start()

        self._jobs[vs_index] = job
        r.started = True
        self._timer.start()

    def _progress_key(self) -> tuple[object, ...]:
        voutput = self._outputs_manager.current_voutput
        store = self._current_store

        return (
            voutput.vs_index if voutput is not None else None,
            self.render_state(),
            store.frame_count if store is not None else 0,
        )

    def _emit_changed(self) -> None:
        self._shown = self._progress_key()
        self.changed.emit()

    @Slot()
    def _on_tick(self) -> None:
        self._retired = [job for job in self._retired if not job.wait(0)]

        for voutput in self._outputs_manager.voutputs:
            vs_index = voutput.vs_index

            if (job := self._jobs.get(vs_index)) is None or self._reported.get(vs_index) is job:
                continue

            progress = job.progress

            if progress.state is JobState.DONE:
                self._reported[vs_index] = job
                self.notificationRequested.emit(
                    Notification(
                        f"Render cache of {voutput.vs_name} is done",
                        Notification.Severity.SUCCESS,
                        self.NOTIFICATION_TITLE,
                        self.NOTIFICATION_KEY,
                    )
                )
            elif progress.state is JobState.FAILED:
                self._reported[vs_index] = job
                error = (progress.error or "").strip().splitlines()
                self.notificationRequested.emit(
                    Notification(
                        f"Render cache of {voutput.vs_name} stopped at frame {progress.failed_frame}: "
                        f"{error[-1] if error else ''}",
                        Notification.Severity.ERROR,
                        self.NOTIFICATION_TITLE,
                        self.NOTIFICATION_KEY,
                    )
                )

        if self._progress_key() != self._shown or any(
            job.progress.state is JobState.RUNNING for job in self._jobs.values()
        ):
            self._emit_changed()

        if not self._retired and all(job.progress.state not in ACTIVE_STATES for job in self._jobs.values()):
            self._timer.stop()


def format_size(size: int) -> str:
    return QLocale().formattedDataSize(size, 1, QLocale.DataSizeFormat.DataSizeSIFormat)


def format_duration(seconds: float) -> str:
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _close_stores(stores: list[DiskFrameStore]) -> None:
    for store in stores:
        store.close()
