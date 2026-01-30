from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from fractions import Fraction
from logging import getLogger
from typing import TYPE_CHECKING, Any

import vapoursynth as vs
from jetpytools import cround

from ..plugins.manager import PluginManager
from ..settings import SettingsManager
from ..utils import LRUCache, cache_clip
from .packing import Packer

if TYPE_CHECKING:
    from ...api._helpers import VideoMetadata
    from ..plugins import PluginAPI
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
        self.vs_output = vs_output
        self.vs_index = vs_index
        self.packer = packer
        self.vs_name = metadata.name if metadata else None

        self.props = LRUCache[int, Mapping[str, Any]](
            cache_size=SettingsManager.global_settings.playback.buffer_size * 2
        )

    def prepare_video(self, api: PluginAPI) -> None:
        clip = self.vs_output.clip.std.ModifyFrame(self.vs_output.clip, self._get_props_on_render)

        if PluginManager.video_processor:
            clip = PluginManager.video_processor(api).prepare(clip)

        if clip.format.id != vs.GRAY32:
            try:
                self.prepared_clip = self.packer.pack_clip(clip)
            except Exception as e:
                raise RuntimeError(f"Failed to pack clip with the message: '{e}'") from e
        else:
            self.prepared_clip = clip

        if cache_size := SettingsManager.global_settings.playback.cache_size:
            try:
                self.prepared_clip = cache_clip(self.prepared_clip, cache_size)
            except Exception as e:
                raise RuntimeError(f"Failed to cache clip with the message: '{e}'") from e

    def clear(self) -> None:
        """Clear VapourSynth resources."""
        self.props.clear()

        for attr in ["vs_output", "prepared_clip"]:
            with suppress(AttributeError):
                delattr(self, attr)

    def time_to_frame(self, time: Time, fps: Fraction | None = None) -> Frame:
        from ..views.timeline import Frame

        if fps is None:
            fps = self.vs_output.clip.fps

        return Frame(cround(time.total_seconds() * fps.numerator / fps.denominator) if fps.denominator > 0 else 0)

    def frame_to_time(self, frame: int, fps: Fraction | None = None) -> Time:
        from ..views.timeline import Time

        if fps is None:
            fps = self.vs_output.clip.fps

        return Time(seconds=frame * fps.denominator / fps.numerator if fps.numerator > 0 else 0)

    def _get_props_on_render(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        self.props[n] = f.props
        return f

    def __del__(self) -> None:
        self.clear()
