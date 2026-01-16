from __future__ import annotations

import gc
from collections import deque
from concurrent.futures import Future
from logging import getLogger
from typing import TYPE_CHECKING, NamedTuple

import vapoursynth as vs
from vsengine.policy import ManagedEnvironment

from ...vsenv import run_in_background
from ..settings import SettingsManager

if TYPE_CHECKING:
    from .video import VideoOutput

logger = getLogger(__name__)


class FrameBundle(NamedTuple):
    """A bundle of frames for a single frame number: main frame + plugin frames."""

    n: int
    main_future: Future[vs.VideoFrame]
    plugin_futures: dict[str, Future[vs.VideoFrame]]


class FrameBuffer:
    """Manages async pre-fetching of video frames during playback."""

    __slots__ = (
        "_bundles",
        "_invalidated",
        "_loop_range",
        "_plugin_nodes",
        "_size",
        "_total_frames",
        "env",
        "video_output",
    )

    def __init__(self, video_output: VideoOutput, env: ManagedEnvironment) -> None:
        self.video_output = video_output
        self.env = env

        self._size = SettingsManager.global_settings.view.buffer_size
        self._bundles = deque[FrameBundle]()
        self._total_frames = 0
        self._loop_range: range | None = None
        self._invalidated = False
        self._plugin_nodes = dict[str, vs.VideoNode]()

        with self.env.use():
            vs.register_on_destroy(self._plugin_nodes.clear)

    @property
    def is_empty(self) -> bool:
        return not self._bundles

    def register_plugin_node(self, identifier: str, node: vs.VideoNode) -> None:
        self._plugin_nodes[identifier] = node
        logger.debug("Registered plugin node: %s", identifier)

    def allocate(self, start_frame: int, total_frames: int, loop_range: range | None = None) -> None:
        self._total_frames = total_frames
        self._loop_range = loop_range

        frames_to_buffer = min(self._size, total_frames - start_frame - 1)
        logger.debug(
            "Allocating buffer: start=%d, buffering %d frames, %d plugins",
            start_frame,
            frames_to_buffer,
            len(self._plugin_nodes),
        )

        current_n = start_frame

        for _ in range(frames_to_buffer):
            next_frame = self._calculate_next_frame(current_n)
            if next_frame is not None:
                self._bundles.appendleft(self._request_bundle(next_frame))
                current_n = next_frame
            else:
                break

    def invalidate(self) -> Future[None]:
        self._invalidated = True
        return self.clear()

    def get_next_frame(self) -> tuple[int, vs.VideoFrame, dict[str, vs.VideoFrame]] | None:
        """
        Get the next buffered frame set (main + plugins) and request a new one at the front.

        Returns None if the next frame isn't ready yet.
        """
        if self._invalidated or not self._bundles:
            return None

        if not self._bundles[-1].main_future.done():
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
        if not self._invalidated and self._bundles:
            next_frame = self._calculate_next_frame(self._bundles[0].n)
            if next_frame is not None:
                self._bundles.appendleft(self._request_bundle(next_frame))

        return bundle.n, main_frame, plugin_frames

    @run_in_background(name="ClearBuffer")
    def clear(self) -> None:
        """Clear all buffered frames and trigger garbage collection."""
        bundles = list(self._bundles)
        self._bundles.clear()

        # Wait for all futures to complete
        for bundle in bundles:
            try:
                if not bundle.main_future.done():
                    bundle.main_future.result()
            except Exception:
                logger.exception("Failed to clear main frame %d", bundle.n)

            for identifier, fut in bundle.plugin_futures.items():
                try:
                    if not fut.done():
                        fut.result()
                except Exception:
                    logger.exception("Failed to clear plugin frame %s:%d", identifier, bundle.n)

        # Close all frames
        for bundle in bundles:
            try:
                if bundle.main_future.done() and not bundle.main_future.exception():
                    (frame := bundle.main_future.result()).close()
                    del frame
            except Exception:
                logger.exception("Failed to close main frame %d", bundle.n)

            for identifier, fut in bundle.plugin_futures.items():
                try:
                    if fut.done() and not fut.exception():
                        (frame := fut.result()).close()
                        del frame
                except Exception:
                    logger.exception("Failed to close plugin frame %s:%d", identifier, bundle.n)

        del bundles
        gc.collect()

        logger.debug("Buffer cleared")

    def _request_bundle(self, n: int) -> FrameBundle:
        plugin_futures = dict[str, Future[vs.VideoFrame]]()

        with self.env.use():
            main_future = self.video_output.prepared_clip.get_frame_async(n)

            for identifier, node in self._plugin_nodes.items():
                plugin_futures[identifier] = node.get_frame_async(n)

        return FrameBundle(n, main_future, plugin_futures)

    def _calculate_next_frame(self, current_frame: int) -> int | None:
        next_frame = current_frame + 1

        if self._loop_range and next_frame >= self._loop_range.stop:
            return self._loop_range.start

        return next_frame if next_frame < self._total_frames else None
