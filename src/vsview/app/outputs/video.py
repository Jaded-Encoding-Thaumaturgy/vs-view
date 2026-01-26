from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from fractions import Fraction
from logging import getLogger
from typing import TYPE_CHECKING, Any

import vapoursynth as vs
from jetpytools import cround

from ..settings import SettingsManager
from .packing import Packer

if TYPE_CHECKING:
    from ...api._helpers import VideoMetadata
    from ..views.timeline import Frame, Time


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
        self.props = LRUCache[int, Mapping[str, Any]](
            cache_size=SettingsManager.global_settings.playback.buffer_size * 2
        )

        try:
            self.prepared_clip = self.packer.pack_clip(self.clip)
        except Exception as e:
            raise RuntimeError(f"Failed to pack clip with the message: '{e}'") from e

        if cache_size := SettingsManager.global_settings.playback.cache_size:
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

    def time_to_frame(self, time: Time, fps: Fraction | None = None) -> Frame:
        from ..views.timeline import Frame

        if fps is None:
            fps = self.clip.fps

        return Frame(cround(time.total_seconds() * fps.numerator / fps.denominator) if fps.denominator > 0 else 0)

    def frame_to_time(self, frame: int, fps: Fraction | None = None) -> Time:
        from ..views.timeline import Time

        if fps is None:
            fps = self.clip.fps

        return Time(seconds=frame * fps.denominator / fps.numerator if fps.numerator > 0 else 0)

    def _get_props_on_render(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        self.props[n] = f.props
        return f

    def __del__(self) -> None:
        self.clear()
