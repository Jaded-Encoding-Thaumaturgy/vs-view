from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, wait
from logging import getLogger
from typing import TYPE_CHECKING, NamedTuple

import vapoursynth as vs
from vsengine.policy import ManagedEnvironment

from ...vsenv import gc_collect, run_in_background
from ..settings import SettingsManager

if TYPE_CHECKING:
    from .audio import AudioOutput
    from .video import VideoOutput

logger = getLogger(__name__)


class FrameBundle(NamedTuple):
    n: int
    main_future: Future[vs.VideoFrame]
    plugin_futures: dict[str, Future[vs.VideoFrame]]


class AudioBundle(NamedTuple):
    n: int
    future: Future[vs.AudioFrame]


def create_iterator(play_range: range) -> Iterator[int]:
    return iter(range(play_range.start + 1, play_range.stop, play_range.step))


class FrameBuffer:
    """Manages async pre-fetching of video frames during playback."""

    __slots__ = (
        "_bundles",
        "_invalidated",
        "_iterator",
        "_loop",
        "_play_range",
        "_plugin_nodes",
        "_size",
        "env",
        "video_output",
    )

    def __init__(self, video_output: VideoOutput, env: ManagedEnvironment) -> None:
        self.video_output = video_output
        self.env = env

        self._size = SettingsManager.global_settings.playback.buffer_size
        self._bundles = deque[FrameBundle]()
        self._play_range: range | None = None
        self._iterator: Iterator[int] | None = None
        self._loop = False
        self._invalidated = False
        self._plugin_nodes = dict[str, vs.VideoNode]()

    def register_plugin_node(self, identifier: str, node: vs.VideoNode) -> None:
        self._plugin_nodes[identifier] = node
        logger.debug("Registered plugin node: %s", identifier)

    def allocate(self, play_range: range, loop: bool = False) -> None:
        self._play_range = play_range
        self._loop = loop

        self._iterator = create_iterator(play_range)

        logger.debug(
            "Allocating buffer: start=%d, step=%d, buffering up to %d frames, %d plugins",
            self._play_range.start + 1,
            self._play_range.step,
            self._size,
            len(self._plugin_nodes),
        )

        for _ in range(self._size):
            if self._invalidated:
                break

            if (next_frame := next(self._iterator, None)) is None:
                break

            self._bundles.appendleft(self._request_bundle(next_frame))

    def wait_for_first_frame(self, timeout: float | None = None, stall_cb: Callable[[], None] | None = None) -> None:
        if self._invalidated or not self._bundles:
            return

        first_frame = self._bundles[-1]

        # Wait for both main frame and all registered plugin frames
        _, undone = wait([first_frame.main_future, *first_frame.plugin_futures.values()], timeout)

        if undone and stall_cb:
            stall_cb()

        for f in [first_frame.main_future, *first_frame.plugin_futures.values()]:
            f.result()

    def invalidate(self) -> Future[None]:
        self._invalidated = True
        return self.clear()

    def get_next_frame(self) -> tuple[int, vs.VideoFrame, dict[str, vs.VideoFrame]] | None:
        """
        Get the next buffered frame set (main + plugins) and request a new one at the front.

        Returns None if the buffer is empty.
        """
        if self._invalidated or not self._bundles:
            return None

        bundle = self._bundles.pop()

        try:
            main_frame = bundle.main_future.result()
        except Exception as e:
            exceptions = [e]
            # Main frame failed - clean up plugin futures to avoid leaks
            for identifier, fut in bundle.plugin_futures.items():
                try:
                    fut.result().close()
                except Exception as ep:
                    exceptions.append(ep)
            raise (
                ExceptionGroup(f"Failed to render main frame '{e}'", exceptions)
                if len(exceptions) > 1
                else exceptions[0]
            )

        # Collect plugin frames (wait for them if not ready yet)
        plugin_frames = dict[str, vs.VideoFrame]()

        for identifier, future in bundle.plugin_futures.items():
            try:
                plugin_frames[identifier] = future.result()
            except Exception:
                logger.exception("Failed to get plugin frame %s for frame %d", identifier, bundle.n)

        # Request next frame set at the front of the buffer (if not invalidated)
        if not self._invalidated:
            next_frame = self._calculate_next_frame()

            if next_frame is not None:
                self._bundles.appendleft(self._request_bundle(next_frame))

        return bundle.n, main_frame, plugin_frames

    @run_in_background(name="ClearBuffer")
    def clear(self) -> None:
        """Clear all buffered frames and trigger garbage collection."""
        self._plugin_nodes.clear()

        bundles = list(self._bundles)
        self._bundles.clear()

        frames_to_close = list[vs.VideoFrame]()

        for bundle in bundles:
            try:
                frame = bundle.main_future.result()
                frames_to_close.append(frame)
            except Exception:
                logger.error("Failed to get main frame %d for cleanup", bundle.n)
                logger.debug("Full traceback:", exc_info=True)

            for identifier, fut in bundle.plugin_futures.items():
                try:
                    frame = fut.result()
                    frames_to_close.append(frame)
                except Exception:
                    logger.error("Failed to get plugin frame %s:%d for cleanup", identifier, bundle.n)
                    logger.debug("Full traceback:", exc_info=True)

        for frame in frames_to_close:
            try:
                frame.close()
            except Exception:
                logger.error("Failed to close frame during cleanup")
                logger.debug("Full traceback:", exc_info=True)

        del frames_to_close
        del bundles
        gc_collect()

        logger.debug("Buffer cleared")

    def _request_bundle(self, n: int) -> FrameBundle:
        plugin_futures = dict[str, Future[vs.VideoFrame]]()

        with self.env.use():
            main_future = self.video_output.prepared_clip.get_frame_async(n)

            for identifier, node in self._plugin_nodes.items():
                plugin_futures[identifier] = node.get_frame_async(n)

        return FrameBundle(n, main_future, plugin_futures)

    def _calculate_next_frame(self) -> int | None:
        if self._iterator:
            next_frame = next(self._iterator, None)

            if next_frame is not None:
                return next_frame

            if next_frame is None and self._loop and self._play_range:
                self._iterator = create_iterator(self._play_range)

                return next(self._iterator)

        return None


class AudioBuffer:
    """Manages async pre-fetching of audio frames during playback."""

    __slots__ = (
        "_bundles",
        "_invalidated",
        "_iterator",
        "_loop",
        "_play_range",
        "_size",
        "audio_output",
        "env",
    )

    def __init__(self, audio_output: AudioOutput, env: ManagedEnvironment) -> None:
        self.audio_output = audio_output
        self.env = env

        self._size = SettingsManager.global_settings.playback.audio_buffer_size
        self._bundles = deque[AudioBundle]()
        self._play_range: range | None = None
        self._iterator: Iterator[int] | None = None
        self._loop = False
        self._invalidated = False

    def allocate(self, play_range: range, loop: bool = False) -> None:
        self._play_range = play_range
        self._loop = loop

        self._iterator = create_iterator(play_range)

        logger.debug(
            "Allocating audio buffer: start=%d, buffering up to %d frames",
            self._play_range.start + 1,
            self._size,
        )

        with self.env.use():
            for _ in range(self._size):
                if self._invalidated:
                    break

                if (next_frame := next(self._iterator, None)) is None:
                    break

                self._bundles.appendleft(
                    AudioBundle(
                        next_frame,
                        self.audio_output.prepared_audio.get_frame_async(next_frame),
                    )
                )

    def wait_for_first_frame(self, timeout: float | None = None, stall_cb: Callable[[], None] | None = None) -> None:
        if self._invalidated or not self._bundles:
            return

        first_frame = self._bundles[-1]

        _, undone = wait([first_frame.future], timeout)

        if undone and stall_cb:
            stall_cb()

        first_frame.future.result()

    def invalidate(self) -> Future[None]:
        self._invalidated = True
        return self.clear()

    def get_next_frame(self) -> tuple[int, vs.AudioFrame] | None:
        """
        Get the next buffered audio frame and request a new one at the front.

        Returns None if the buffer is empty.
        """
        if self._invalidated or not self._bundles:
            return None

        bundle = self._bundles.pop()

        try:
            frame = bundle.future.result()
        except Exception:
            logger.exception("Failed to get audio frame %d", bundle.n)
            return None

        # Request next frame at the front of the buffer
        if not self._invalidated:
            next_frame = self._calculate_next_frame()

            if next_frame is not None:
                with self.env.use():
                    self._bundles.appendleft(
                        AudioBundle(next_frame, self.audio_output.prepared_audio.get_frame_async(next_frame))
                    )

        return bundle.n, frame

    @run_in_background(name="ClearAudioBuffer")
    def clear(self) -> None:
        """Clear all buffered frames and trigger garbage collection."""
        bundles = list(self._bundles)
        self._bundles.clear()

        frames_to_close = list[vs.AudioFrame]()

        for bundle in bundles:
            try:
                frame = bundle.future.result()
                frames_to_close.append(frame)
            except Exception:
                logger.error("Failed to get audio frame for cleanup")
                logger.debug("Full traceback:", exc_info=True)

        for frame in frames_to_close:
            try:
                frame.close()
            except Exception:
                logger.error("Failed to close audio frame during cleanup")
                logger.debug("Full traceback:", exc_info=True)

        del frames_to_close
        del bundles
        gc_collect()

        logger.debug("Audio buffer cleared")

    def _calculate_next_frame(self) -> int | None:
        if self._iterator:
            next_frame = next(self._iterator, None)

            if next_frame is not None:
                return next_frame

            if next_frame is None and self._loop and self._play_range:
                self._iterator = create_iterator(self._play_range)

                return next(self._iterator)

        return None
