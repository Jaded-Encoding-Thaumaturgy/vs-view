from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

import pytest
import vapoursynth as vs
from vsengine.policy import ManagedEnvironment

from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.buffer import AudioBuffer, AudioBundle, FrameBuffer, FrameBundle
from vsview.app.outputs.video import VideoOutput
from vsview.app.settings import SettingsManager

pytestmark = [pytest.mark.unit, pytest.mark.vpy("no-core")]


@pytest.fixture
def env(vpy_env_factory: Callable[[], ManagedEnvironment]) -> ManagedEnvironment:
    return vpy_env_factory()


@pytest.fixture
def video_output(
    env: ManagedEnvironment,
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> VideoOutput:
    return create_test_video_output(env, 50)


@pytest.fixture
def audio_output(env: ManagedEnvironment) -> AudioOutput:
    with env.use():
        raw_audio = env.core.std.BlankAudio(length=30720)  # 10 audio frames at 3072 samples/frame
        ao = AudioOutput(raw_audio, 0, None)
        ao.playback_audio = raw_audio
    return ao


class DelegatingVideoNode:
    """Delegating wrapper for vs.VideoNode to intercept futures in tests."""

    def __init__(
        self,
        real_node: vs.VideoNode,
        on_future: Callable[[Future[vs.VideoFrame]], None] | None = None,
    ) -> None:
        self._real = real_node
        self._on_future = on_future

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def get_frame_async(self, n: int) -> Future[vs.VideoFrame]:
        fut = self._real.get_frame_async(n)
        if self._on_future:
            self._on_future(fut)
        return fut


class PendingAudioNode:
    """Delegating wrapper to control completion of get_frame_async for testing."""

    def __init__(self, real_node: vs.AudioNode, future: Future[vs.AudioFrame]) -> None:
        self._real = real_node
        self._future = future

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def get_frame_async(self, n: int) -> Future[vs.AudioFrame]:
        return self._future


# FrameBundle & AudioBundle Tests
def test_bundles_namedtuples() -> None:
    main_fut = Future[vs.VideoFrame]()
    plugin_fut = Future[vs.VideoFrame]()
    bundle = FrameBundle(n=1, main_future=main_fut, plugin_futures={"plug": plugin_fut})

    assert bundle.n == 1
    assert bundle.main_future is main_fut
    assert bundle.plugin_futures == {"plug": plugin_fut}

    a_fut = Future[vs.AudioFrame]()
    abundle = AudioBundle(n=0, future=a_fut)

    assert abundle.n == 0
    assert abundle.future is a_fut


# FrameBuffer Tests
def test_frame_buffer_init(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)

    assert fb.video_output is video_output
    assert fb.env is env
    assert fb.get_next_frame() is None


def test_frame_buffer_register_plugin_node(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        plugin_node = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)

    fb.register_plugin_node("test_plugin", plugin_node)
    fb.allocate(range(5))

    result = fb.get_next_frame()
    assert result is not None
    n, main_frame, plugin_frames = result
    assert n == 1
    assert (
        main_frame.width,
        main_frame.height,
        main_frame.format.id,
    ) == (
        plugin_node.width,
        plugin_node.height,
        int(vs.GRAY32),
    )
    assert list(plugin_frames.keys()) == ["test_plugin"]
    assert isinstance(plugin_frames["test_plugin"], vs.VideoFrame)


def test_frame_buffer_allocate_normal_range(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)

    # Frame 0 is skipped by _create_play_frames, so range(0, 10) yields frames 1..9 (9 frames total).
    fb.allocate(range(10))

    drained_frames = list[int]()
    while (result := fb.get_next_frame()) is not None:
        drained_frames.append(result[0])

    assert drained_frames == list(range(1, 10))
    assert len(drained_frames) == 9
    assert fb.get_next_frame() is None

    # Test with range larger than default buffer size (range(0, 30) -> 29 frames total)
    fb2 = FrameBuffer(video_output, env)
    fb2.allocate(range(30))

    drained_frames2 = list[int]()
    while (result2 := fb2.get_next_frame()) is not None:
        drained_frames2.append(result2[0])

    assert drained_frames2 == list(range(1, 30))
    assert len(drained_frames2) == 29
    assert fb2.get_next_frame() is None


def test_frame_buffer_allocate_loop(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    buffer_size = SettingsManager.global_settings.playback.buffer_size

    # range(0, 3) skips frame 0, yielding cycle([1, 2]).
    fb.allocate(range(3), loop=True)

    expected_frames = [1 if i % 2 == 0 else 2 for i in range(buffer_size * 2)]
    drained_frames = list[int]()
    for _ in range(buffer_size * 2):
        result = fb.get_next_frame()
        assert result is not None
        drained_frames.append(result[0])

    assert drained_frames == expected_frames


def test_frame_buffer_allocate_when_invalidated(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    fut = fb.invalidate()
    fut.result()

    fb.allocate(range(10))
    assert fb.get_next_frame() is None


def test_frame_buffer_get_next_frame_normal(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        plugin_node = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=50)
    fb.register_plugin_node("plugin_a", plugin_node)

    fb.allocate(range(30))

    result = fb.get_next_frame()
    assert result is not None
    n, main_frame, plugin_frames = result

    assert n == 1
    assert (
        main_frame.width,
        main_frame.height,
        main_frame.format.id,
    ) == (
        plugin_node.width,
        plugin_node.height,
        int(vs.GRAY32),
    )
    assert list(plugin_frames.keys()) == ["plugin_a"]
    assert isinstance(plugin_frames["plugin_a"], vs.VideoFrame)

    # Subsequent frame continues yielding sequentially via pre-fetch
    result2 = fb.get_next_frame()
    assert result2 is not None
    n2, main_frame2, plugin_frames2 = result2
    assert n2 == 2
    assert (
        main_frame2.width,
        main_frame2.height,
        main_frame2.format.id,
    ) == (
        plugin_node.width,
        plugin_node.height,
        int(vs.GRAY32),
    )
    assert list(plugin_frames2.keys()) == ["plugin_a"]
    assert isinstance(plugin_frames2["plugin_a"], vs.VideoFrame)


def test_frame_buffer_get_next_frame_empty(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    assert fb.get_next_frame() is None


def test_frame_buffer_get_next_frame_invalidated(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    fb.allocate(range(10))
    fut = fb.invalidate()
    fut.result()

    assert fb.get_next_frame() is None


def test_frame_buffer_get_next_frame_main_error_closes_plugin_futures(
    video_output: VideoOutput, env: ManagedEnvironment
) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        base_clip = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
        video_output.prepared_clip = env.core.std.FrameEval(
            base_clip,
            lambda n: base_clip if n == 0 else (_ for _ in ()).throw(RuntimeError("Main frame evaluation failed")),
        )
        plugin_clip = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)

    saved_plugin_frames = list[vs.VideoFrame]()
    wrapped_plugin_node = DelegatingVideoNode(
        plugin_clip,
        on_future=lambda fut: fut.add_done_callback(lambda f: saved_plugin_frames.append(f.result())),
    )
    fb.register_plugin_node("plug", wrapped_plugin_node)  # type: ignore[arg-type]

    fb.allocate(range(2))

    with pytest.raises(vs.Error, match=r"Main frame evaluation failed"):
        fb.get_next_frame()

    assert len(saved_plugin_frames) == 1
    with pytest.raises(RuntimeError, match=r"The Frame has already been released"):
        saved_plugin_frames[0].get_read_ptr(0)


def test_frame_buffer_get_next_frame_main_and_plugin_error_exception_group(
    video_output: VideoOutput, env: ManagedEnvironment
) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        base_clip = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
        video_output.prepared_clip = env.core.std.FrameEval(
            base_clip,
            lambda n: base_clip if n == 0 else (_ for _ in ()).throw(RuntimeError("Main frame failed")),
        )
        failing_plugin = env.core.std.FrameEval(
            base_clip,
            lambda n: base_clip if n == 0 else (_ for _ in ()).throw(ValueError("Plugin failed")),
        )

    fb.register_plugin_node("plug", failing_plugin)
    fb.allocate(range(2))

    with pytest.raises(ExceptionGroup, match=r"Failed to render main frame") as exc_info:
        fb.get_next_frame()

    assert len(exc_info.value.exceptions) == 2
    assert isinstance(exc_info.value.exceptions[0], vs.Error)
    assert "Main frame failed" in str(exc_info.value.exceptions[0])
    assert isinstance(exc_info.value.exceptions[1], vs.Error)
    assert "Plugin failed" in str(exc_info.value.exceptions[1])


def test_frame_buffer_get_next_frame_plugin_error_logged(
    video_output: VideoOutput, env: ManagedEnvironment, caplog: pytest.LogCaptureFixture
) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        base_clip = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
        failing_plugin = env.core.std.FrameEval(
            base_clip,
            lambda n: base_clip if n == 0 else (_ for _ in ()).throw(RuntimeError("Plugin internal error")),
        )

    fb.register_plugin_node("failing_plugin", failing_plugin)
    fb.allocate(range(2))

    with caplog.at_level(logging.ERROR):
        result = fb.get_next_frame()

    assert result is not None
    n, main_frame, plugin_frames = result
    assert n == 1
    assert isinstance(main_frame, vs.VideoFrame)
    assert plugin_frames == {}
    assert "Failed to get plugin frame failing_plugin for frame 1" in caplog.text


def test_frame_buffer_wait_for_first_frame_normal(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    fb.allocate(range(5))

    stall_called = False

    def stall_cb() -> None:
        nonlocal stall_called
        stall_called = True

    fb.wait_for_first_frame(timeout=5.0, stall_cb=stall_cb)
    assert stall_called is False


def test_frame_buffer_wait_for_first_frame_timeout_stall_cb(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    event = threading.Event()
    with env.use():
        base_clip = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)
        video_output.prepared_clip = env.core.std.FrameEval(
            base_clip,
            lambda n: (event.wait(timeout=5.0), base_clip)[1],
        )

    fb = FrameBuffer(video_output, env)
    fb.allocate(range(2))

    stall_called = False

    def stall_cb() -> None:
        nonlocal stall_called
        stall_called = True
        event.set()

    fb.wait_for_first_frame(timeout=0.01, stall_cb=stall_cb)
    assert stall_called is True


def test_frame_buffer_wait_for_first_frame_empty_or_invalidated(
    video_output: VideoOutput, env: ManagedEnvironment
) -> None:
    fb = FrameBuffer(video_output, env)
    stall_called = False

    def stall_cb() -> None:
        nonlocal stall_called
        stall_called = True

    # Empty buffer: returns immediately
    fb.wait_for_first_frame(timeout=1.0, stall_cb=stall_cb)
    assert stall_called is False

    # Invalidated buffer: returns immediately
    fb.allocate(range(5))
    fut = fb.invalidate()
    fut.result()
    fb.wait_for_first_frame(timeout=1.0, stall_cb=stall_cb)
    assert stall_called is False


def test_frame_buffer_invalidate_and_clear(video_output: VideoOutput, env: ManagedEnvironment) -> None:
    fb = FrameBuffer(video_output, env)
    with env.use():
        plugin_node = env.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=20)
    fb.register_plugin_node("plug", plugin_node)
    fb.allocate(range(10))

    result = fb.get_next_frame()
    assert result is not None
    assert result[0] == 1

    fut = fb.invalidate()
    assert isinstance(fut, Future)
    fut.result()

    assert fb.get_next_frame() is None
    fb.allocate(range(10))
    assert fb.get_next_frame() is None


# AudioBuffer Tests
def test_audio_buffer_init(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)

    assert ab.audio_output is audio_output
    assert ab.env is env
    assert ab.get_next_frame() is None


def test_audio_buffer_allocate(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)

    # AudioBuffer does not skip the first frame
    ab.allocate(range(5))

    ab.wait_for_first_frame(timeout=5.0)
    res = ab.get_next_frame()
    assert res is not None
    assert res[0] == 0
    assert isinstance(res[1], vs.AudioFrame)

    # Test loop=True
    ab_loop = AudioBuffer(audio_output, env)
    ab_loop.allocate(range(2), loop=True)

    ab_loop.wait_for_first_frame(timeout=5.0)
    res_0 = ab_loop.get_next_frame()
    assert res_0 is not None
    assert res_0[0] == 0

    ab_loop.wait_for_first_frame(timeout=5.0)
    res_1 = ab_loop.get_next_frame()
    assert res_1 is not None
    assert res_1[0] == 1

    ab_loop.wait_for_first_frame(timeout=5.0)
    res_loop = ab_loop.get_next_frame()
    assert res_loop is not None
    assert res_loop[0] == 0

    # Test already invalidated
    ab_inv = AudioBuffer(audio_output, env)
    fut = ab_inv.invalidate()
    fut.result()
    ab_inv.allocate(range(10))
    assert ab_inv.get_next_frame() is None


def test_audio_buffer_get_next_frame_normal(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)
    ab.allocate(range(10))

    # Wait for the first future to be done so get_next_frame returns it
    ab.wait_for_first_frame(timeout=5.0)

    res = ab.get_next_frame()
    assert res is not None
    n, frame = res
    assert n == 0
    assert isinstance(frame, vs.AudioFrame)

    ab.wait_for_first_frame(timeout=5.0)
    res2 = ab.get_next_frame()
    assert res2 is not None
    n2, frame2 = res2
    assert n2 == 1
    assert isinstance(frame2, vs.AudioFrame)


def test_audio_buffer_get_next_frame_not_done(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    raw_audio = audio_output.playback_audio
    pending_fut = Future[vs.AudioFrame]()
    audio_output.playback_audio = PendingAudioNode(raw_audio, pending_fut)  # type: ignore[assignment]

    ab = AudioBuffer(audio_output, env)
    ab.allocate(range(1))

    # If future is not done, returns None and does not pop
    assert ab.get_next_frame() is None

    with env.use():
        real_frame = raw_audio.get_frame(0)
    pending_fut.set_result(real_frame)

    res = ab.get_next_frame()
    assert res is not None
    assert res[0] == 0
    assert res[1] is real_frame

    # Once drained, buffer returns None
    assert ab.get_next_frame() is None


def test_audio_buffer_get_next_frame_empty_and_invalidated(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)
    assert ab.get_next_frame() is None

    ab.allocate(range(5))
    fut = ab.invalidate()
    fut.result()
    assert ab.get_next_frame() is None


def test_audio_buffer_get_next_frame_exception(
    audio_output: AudioOutput,
    env: ManagedEnvironment,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_audio = audio_output.playback_audio
    err_fut = Future[vs.AudioFrame]()
    err_fut.set_exception(RuntimeError("Audio frame error"))
    monkeypatch.setattr(audio_output, "playback_audio", PendingAudioNode(raw_audio, err_fut))

    ab = AudioBuffer(audio_output, env)
    ab.allocate(range(3, 4))

    with caplog.at_level(logging.ERROR):
        res = ab.get_next_frame()

    assert res is None
    assert "Failed to get audio frame 3" in caplog.text


def test_audio_buffer_wait_for_first_frame(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)
    ab.allocate(range(5))

    stall_called = False

    def stall_cb() -> None:
        nonlocal stall_called
        stall_called = True

    ab.wait_for_first_frame(timeout=5.0, stall_cb=stall_cb)
    assert stall_called is False

    # Empty buffer: returns immediately
    ab_empty = AudioBuffer(audio_output, env)
    stall_called_empty = False

    def stall_cb_empty() -> None:
        nonlocal stall_called_empty
        stall_called_empty = True

    ab_empty.wait_for_first_frame(timeout=1.0, stall_cb=stall_cb_empty)
    assert stall_called_empty is False

    # Timeout case
    raw_audio = audio_output.playback_audio
    pending_fut = Future[vs.AudioFrame]()
    audio_output.playback_audio = PendingAudioNode(raw_audio, pending_fut)  # type: ignore[assignment]

    with env.use():
        real_frame = raw_audio.get_frame(0)

    stall_called_timeout = False

    def stall_cb_timeout() -> None:
        nonlocal stall_called_timeout
        stall_called_timeout = True
        pending_fut.set_result(real_frame)

    ab_stall = AudioBuffer(audio_output, env)
    ab_stall.allocate(range(1))
    ab_stall.wait_for_first_frame(timeout=0.001, stall_cb=stall_cb_timeout)
    assert stall_called_timeout is True


def test_audio_buffer_invalidate_and_clear(audio_output: AudioOutput, env: ManagedEnvironment) -> None:
    ab = AudioBuffer(audio_output, env)
    ab.allocate(range(5))

    ab.wait_for_first_frame(timeout=5.0)
    assert ab.get_next_frame() is not None

    fut = ab.invalidate()
    assert isinstance(fut, Future)
    fut.result()

    assert ab.get_next_frame() is None
    ab.allocate(range(5))
    assert ab.get_next_frame() is None
