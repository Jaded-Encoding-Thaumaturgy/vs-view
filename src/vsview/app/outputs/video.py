from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from logging import getLogger
from typing import TYPE_CHECKING, Any

import vapoursynth as vs

from ..settings import SettingsManager
from .packing import Packer

if TYPE_CHECKING:
    from ...api._helpers import VideoMetadata

logger = getLogger(__name__)


class VideoOutput:
    def __init__(
        self,
        vs_output: vs.VideoOutputTuple,
        vs_index: int,
        packer: Packer,
        metadata: VideoMetadata | None = None,
    ) -> None:
        from ..utils import LRUCache, cache_clip

        self.packer = packer
        self.vs_index = vs_index
        self.vs_name = metadata.name if metadata else None
        # self.alpha = metadata.alpha if metadata else None

        self.vs_output = vs_output
        self.clip = self.vs_output.clip.std.ModifyFrame(self.vs_output.clip, self._get_props_on_render)
        self.props = LRUCache[int, Mapping[str, Any]](cache_size=SettingsManager.global_settings.view.buffer_size * 2)

        try:
            self.prepared_clip = self.packer.pack_clip(self.clip)
        except Exception as e:
            raise RuntimeError(f"Failed to pack clip with the message: '{e}'") from e

        if cache_size := SettingsManager.global_settings.view.cache_size:
            try:
                self.prepared_clip = cache_clip(self.prepared_clip, cache_size)
            except Exception as e:
                raise RuntimeError(f"Failed to cache clip with the message: '{e}'") from e

    def clear(self) -> None:
        """Clear VapourSynth resources."""
        if hasattr(self, "props"):
            self.props.clear()

        for attr in ["vs_output", "clip", "prepared_clip"]:
            with suppress(AttributeError):
                delattr(self, attr)

    def request_qimage(self, n: int, env: ManagedEnvironment) -> QImage:
        """Request a QImage synchronously."""

        with self.request_frame(n, env) as frame:
            return self.packer.frame_to_qimage(frame)

    def request_frame(self, n: int, env: ManagedEnvironment) -> vs.VideoFrame:
        """Request a frame synchronously. Remember to close the frame when done."""
        with env.use():
            return self.prepared_clip.get_frame(n)

    def request_frame_async(self, n: int, env: ManagedEnvironment) -> Future[vs.VideoFrame]:
        """Request a frame asynchronously without blocking. Remember to close the frame when done."""
        with env.use():
            return self.prepared_clip.get_frame_async(n)

    def _get_props_on_render(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        self.props[n] = f.props
        return f

    def __del__(self) -> None:
        self.clear()
