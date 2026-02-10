from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from contextlib import suppress
from fractions import Fraction
from itertools import accumulate
from logging import getLogger
from typing import TYPE_CHECKING, Any, Literal

import vapoursynth as vs
from jetpytools import cround

from ..plugins.manager import PluginManager
from ..settings import SettingsManager
from ..utils import LRUCache, cache_clip
from .packing import AlphaNotImplementedError, CythonPacker, Packer

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
        self.framedurs = metadata.framedurs if metadata else None
        self._alpha_prop: Literal[True] | None = metadata.alpha_prop if metadata else None

        self.cum_durations = (
            list(accumulate(self.framedurs))
            if self.framedurs
            else list(accumulate([float(1 / self.vs_output.clip.fps)] * self.vs_output.clip.num_frames))
            if self.vs_output.clip.fps > 0
            else None
        )

        self.props = LRUCache[int, Mapping[str, Any]](
            cache_size=SettingsManager.global_settings.playback.buffer_size * 2
        )

        self.last_frame = 0
        self.loaded_once = False

    def prepare_video(self, api: PluginAPI) -> None:
        clip = self.vs_output.clip.std.ModifyFrame(self.vs_output.clip, self._get_props_on_render)

        if PluginManager.video_processor:
            clip = PluginManager.video_processor(api).prepare(clip)

        if clip.format.id == vs.GRAY32:
            self.prepared_clip = clip
        else:
            try:
                try:
                    self.prepared_clip = self.packer.pack_clip(clip, self.vs_output.alpha or self._alpha_prop)
                except AlphaNotImplementedError as e:
                    logger.warning("%s, falling back to Cython 8-bit", e)
                    self.packer = CythonPacker(8)
                    self.prepared_clip = self.packer.pack_clip(clip, self.vs_output.alpha or self._alpha_prop)
            except Exception as e:
                raise RuntimeError(f"Failed to pack clip with the message: '{e}'") from e

        if cache_size := SettingsManager.global_settings.playback.cache_size:
            try:
                self.prepared_clip = cache_clip(self.prepared_clip, cache_size)
            except Exception as e:
                raise RuntimeError(f"Failed to cache clip with the message: '{e}'") from e
        else:
            self.prepared_clip.std.SetVideoCache(0)

    def clear(self) -> None:
        """Clear VapourSynth resources."""
        self.props.clear()

        for attr in ["vs_output", "prepared_clip"]:
            with suppress(AttributeError):
                delattr(self, attr)

    def time_to_frame(self, time: Time, fps: VideoOutput | Fraction | None = None) -> Frame:
        from ..views.timeline import Frame

        # So VideoOutputProxy can get this method
        fps, cum_durations = VideoOutput._get_fps_and_durations(self, fps)

        if fps == 0 and cum_durations:
            return Frame(bisect_right(cum_durations, time.total_seconds()))

        return Frame(cround(time.total_seconds() * fps) if fps > 0 else 0)

    def frame_to_time(self, frame: int, fps: VideoOutput | Fraction | None = None) -> Time:
        from ..views.timeline import Time

        # So VideoOutputProxy can get this method
        fps, cum_durations = VideoOutput._get_fps_and_durations(self, fps)

        if fps == 0 and cum_durations:
            return Time(seconds=cum_durations[frame - 1] if frame > 0 else 0)

        return Time(seconds=frame * fps.denominator / fps.numerator if fps > 0 else 0)

    def _get_fps_and_durations(self, fps: VideoOutput | Fraction | None) -> tuple[Fraction, list[float] | None]:
        if fps is None:
            return self.vs_output.clip.fps, self.cum_durations

        if isinstance(fps, Fraction):
            return fps, self.cum_durations

        return fps.vs_output.clip.fps, fps.cum_durations

    def _get_props_on_render(self, n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        self.props[n] = f.props
        return f

    def __del__(self) -> None:
        self.clear()
