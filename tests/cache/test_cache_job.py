from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import override

import pytest
import vapoursynth as vs
from vsengine.policy import ManagedEnvironment

from vsview.app.cache import OutputCache
from vsview.app.cache.job import JobState, RenderJob
from vsview.app.cache.storage import CacheSession, DiskFrameStore

pytestmark = [pytest.mark.unit, pytest.mark.vpy("no-core")]

TIMEOUT = 10.0


class _GatedClip:
    """A source whose frames are held back until the test opens their gate, and which records each request."""

    def __init__(self, clip: vs.VideoNode, *, broken: frozenset[int] = frozenset()) -> None:
        self.requested = list[int]()
        self.broken = broken
        self._cond = threading.Condition()
        self._open = set[int]()
        self._all_open = False
        self.clip = clip.std.ModifyFrame(clip, self._eval)

    def open(self, *frames: int) -> None:
        with self._cond:
            self._open.update(frames)
            self._cond.notify_all()

    def open_all(self) -> None:
        with self._cond:
            self._all_open = True
            self._cond.notify_all()

    def wait_requested(self, count: int) -> list[int]:
        with self._cond:
            assert self._cond.wait_for(lambda: len(self.requested) >= count, TIMEOUT)
            return list(self.requested)

    def _eval(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        with self._cond:
            self.requested.append(n)
            self._cond.notify_all()
            self._cond.wait_for(lambda: self._all_open or n in self._open, TIMEOUT)

        if n in self.broken:
            raise ValueError(f"broken frame {n}")

        return f


class _GatedStore(DiskFrameStore):
    """A store whose disk writes are held back until the test opens their gate."""

    def __init__(self, session: CacheSession, clip: vs.VideoNode) -> None:
        path = session.path / "gated"
        path.mkdir()
        super().__init__(session, path, clip)
        self._cond = threading.Condition()
        self._open = set[int]()

    def open(self, *frames: int) -> None:
        with self._cond:
            self._open.update(frames)
            self._cond.notify_all()

    @override
    def write(self, n: int, frame: vs.VideoFrame) -> None:
        with self._cond:
            self._cond.wait_for(lambda: n in self._open, TIMEOUT)
        super().write(n, frame)


def _planes(frame: vs.VideoFrame) -> list[bytes]:
    return [bytes(frame[p]) for p in range(frame.format.num_planes)]


@pytest.fixture
def env(vpy_env_factory: Callable[[], ManagedEnvironment]) -> ManagedEnvironment:
    return vpy_env_factory()


@pytest.fixture
def source(env: ManagedEnvironment, patterned_clip: Callable[..., vs.VideoNode]) -> vs.VideoNode:
    with env.use():
        return patterned_clip(vs.GRAY8, 16, 16)


@pytest.fixture
def jobs(env: ManagedEnvironment, session: CacheSession) -> Iterator[list[RenderJob]]:
    """Jobs created by a test. They are cancelled at teardown, before the session and the environment go away."""
    created = list[RenderJob]()
    yield created
    for job in created:
        assert job.cancel(TIMEOUT)


def _job(
    jobs: list[RenderJob],
    env: ManagedEnvironment,
    session: CacheSession,
    clip: vs.VideoNode,
    frames: range,
    *,
    concurrency: int = 2,
) -> RenderJob:
    with env.use():
        cache = OutputCache(clip)
    job = RenderJob(cache, cache.ensure_store(session), env.use, frames, concurrency=concurrency)
    jobs.append(job)
    return job


def test_job_renders_the_whole_range(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    job = _job(jobs, env, session, source, range(2, 8))

    job.start()
    assert job.wait(TIMEOUT)

    assert job.store.frames() == [2, 3, 4, 5, 6, 7]
    assert job.progress.state is JobState.DONE

    with env.use():
        assert _planes(job.store.read(4)) == _planes(source.get_frame(4))


def test_job_skips_frames_that_are_already_cached(
    jobs: list[RenderJob],
    env: ManagedEnvironment,
    session: CacheSession,
    source: vs.VideoNode,
    patterned_clip: Callable[..., vs.VideoNode],
) -> None:
    with env.use():
        gated = _GatedClip(source)
        other = patterned_clip(vs.GRAY8, 16, 16, seed="other")
    job = _job(jobs, env, session, gated.clip, range(4))
    with env.use():
        job.store.write(1, other.get_frame(1))

    gated.open_all()
    job.start()
    assert job.wait(TIMEOUT)

    assert sorted(gated.requested) == [0, 2, 3]
    assert job.progress.state is JobState.DONE


def test_job_keeps_at_most_concurrency_frames_in_flight(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    with env.use():
        gated = _GatedClip(source)
        cache = OutputCache(gated.clip)
    gated.open_all()
    store = _GatedStore(session, cache.source)
    job = RenderJob(cache, store, env.use, range(6), concurrency=2)
    jobs.append(job)

    job.start()
    first = sorted(gated.wait_requested(2))
    store.open(0)
    after_one_write = sorted(gated.wait_requested(3))
    store.open(*range(6))
    assert job.wait(TIMEOUT)

    assert first == [0, 1]
    assert after_one_write == [0, 1, 2]
    assert store.frames() == [0, 1, 2, 3, 4, 5]


def test_raising_concurrency_requests_more_frames_at_once(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    with env.use():
        gated = _GatedClip(source)
        cache = OutputCache(gated.clip)
    gated.open_all()
    store = _GatedStore(session, cache.source)
    job = RenderJob(cache, store, env.use, range(6), concurrency=1)
    jobs.append(job)

    job.start()
    first = gated.wait_requested(1)
    job.set_concurrency(3)
    after_raise = sorted(gated.wait_requested(3))
    store.open(*range(6))
    assert job.wait(TIMEOUT)

    assert first == [0]
    assert after_raise == [0, 1, 2]
    assert store.frames() == [0, 1, 2, 3, 4, 5]


def test_pause_stops_new_requests_until_resume(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    with env.use():
        gated = _GatedClip(source)
    job = _job(jobs, env, session, gated.clip, range(3), concurrency=1)

    job.start()
    gated.wait_requested(1)
    job.pause()
    gated.open_all()
    assert job.wait(TIMEOUT)

    assert job.progress.state is JobState.PAUSED
    assert job.store.frames() == [0]
    assert gated.requested == [0]

    job.resume()
    assert job.wait(TIMEOUT)

    assert job.progress.state is JobState.DONE
    assert job.store.frames() == [0, 1, 2]


def test_resume_does_not_end_an_automatic_pause(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    job = _job(jobs, env, session, source, range(3))

    job.set_waiting(True)
    job.start()
    job.pause()
    job.resume()

    assert job.progress.state is JobState.WAITING
    assert job.store.frame_count == 0

    job.set_waiting(False)
    assert job.wait(TIMEOUT)

    assert job.progress.state is JobState.DONE


def test_cancel_writes_none_of_the_frames_in_flight(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    with env.use():
        gated = _GatedClip(source)
    job = _job(jobs, env, session, gated.clip, range(6), concurrency=2)

    job.start()
    gated.wait_requested(1)
    finished_at_once = job.cancel(timeout=0)
    gated.open_all()
    assert job.wait(TIMEOUT)

    assert not finished_at_once
    assert job.progress.state is JobState.CANCELLED
    assert job.store.frame_count == 0
    assert sorted(gated.requested) == [0, 1]


def test_failed_frame_stops_the_job_and_keeps_written_frames(
    jobs: list[RenderJob], env: ManagedEnvironment, session: CacheSession, source: vs.VideoNode
) -> None:
    with env.use():
        gated = _GatedClip(source, broken=frozenset({3}))
    job = _job(jobs, env, session, gated.clip, range(6), concurrency=1)

    gated.open_all()
    job.start()
    assert job.wait(TIMEOUT)

    progress = job.progress
    assert progress.state is JobState.FAILED
    assert progress.failed_frame == 3
    assert progress.error is not None
    assert "broken frame 3" in progress.error
    assert job.store.frames() == [0, 1, 2]
    assert gated.requested == [0, 1, 2, 3]


def test_quota_stops_the_job(
    jobs: list[RenderJob], env: ManagedEnvironment, source: vs.VideoNode, tmp_path: Path
) -> None:
    with env.use():
        cache = OutputCache(source)
    probe = CacheSession(tmp_path / "probe", quota_bytes=1 << 30)
    with env.use():
        probe.create_store(cache.source).write(0, source.get_frame(0))
    frame_size = probe.used_bytes
    probe.close()
    session = CacheSession(tmp_path / "cache", quota_bytes=frame_size * 2 + frame_size // 2)
    job = RenderJob(cache, cache.ensure_store(session), env.use, range(5), concurrency=1)
    jobs.append(job)

    job.start()
    assert job.wait(TIMEOUT)

    assert job.progress.state is JobState.FAILED
    assert job.progress.failed_frame == 2
    assert job.store.frames() == [0, 1]
    session.close()
