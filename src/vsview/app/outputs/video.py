from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import timedelta
from fractions import Fraction
from itertools import accumulate
from logging import getLogger
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import vapoursynth as vs
from jetpytools import cround

from ...types import Frame, Time
from ..packing import Packer
from ..settings import SettingsManager
from ..utils import LRUCache, cache_clip

if TYPE_CHECKING:
    from ..plugins import PluginAPI
    from ..views.status import OutputInfo


logger = getLogger(__name__)


class VideoMetadata(NamedTuple):
    name: str
    framedurs: Sequence[float] | None
    alpha_prop: Literal[True] | None
    hdr: bool | None
    kwargs: dict[str, Any]


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
        self.vs_name = metadata.name if metadata else f"Clip {vs_index}"  # Matches vsview.set_output
        self.framedurs = metadata.framedurs if metadata else None
        self.kwargs = metadata.kwargs if metadata else {}
        self._alpha_prop: Literal[True] | None = metadata.alpha_prop if metadata else None

        if self._alpha_prop:
            try:
                alpha_plane = self.vs_output.clip.std.PropToClip("_Alpha")
            except vs.Error:
                logger.warning("Alpha plane not found")
            else:
                self.vs_output = self.vs_output._replace(alpha=alpha_plane)

        if self.framedurs:
            self.cum_durations: list[float] | None = list(accumulate(self.framedurs))
        elif self.vs_output.clip.fps > 0:
            self.cum_durations = [
                float(1 / self.vs_output.clip.fps) * i for i in range(1, self.vs_output.clip.num_frames + 1)
            ]
        else:
            self.cum_durations = None

        self.props = LRUCache[int, Mapping[str, Any]](
            cache_size=SettingsManager.global_settings.playback.buffer_size * 2
        )

        self.last_frame = 0
        self.loaded_once = False

    @property
    def info(self) -> OutputInfo:
        from ..views.status import OutputInfo

        if self.vs_output.clip.fps.numerator > 0:
            total_duration = Time(seconds=float(self.vs_output.clip.num_frames * 1 / self.vs_output.clip.fps))
            fps = self.vs_output.clip.fps
        elif self.cum_durations:
            total_duration = Time(seconds=self.cum_durations[-1])
            fps = self.vs_output.clip.num_frames / total_duration.total_seconds()
        else:
            total_duration = Time()
            fps = 0

        sar = 1.0
        if props := self.props.get(self.last_frame):
            sar_num, sar_den = props.get("_SARNum"), props.get("_SARDen")

            if isinstance(sar_num, int) and isinstance(sar_den, int):
                sar = sar_num / sar_den

        return OutputInfo(
            total_duration=total_duration,
            total_frames=self.vs_output.clip.num_frames,
            width=self.vs_output.clip.width,
            height=self.vs_output.clip.height,
            format_name=self.vs_output.clip.format.name if self.vs_output.clip.format else "NONE",
            fps=fps,
            sar=sar,
        )

    def prepare_video(self, api: PluginAPI) -> None:
        from ..plugins.manager import PluginManager

        clip = self.vs_output.clip.std.ModifyFrame(self.vs_output.clip, self._get_props_on_render)

        if PluginManager.video_processor:
            clip = PluginManager.video_processor(api).prepare(clip)

        if clip.format.id == vs.GRAY32:
            self.prepared_clip = clip
        else:
            try:
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

        for attr in ["vs_output", "prepared_clip", "kwargs"]:
            with suppress(AttributeError):
                delattr(self, attr)

    def time_to_frame(self, time: timedelta, fps: VideoOutput | Fraction | None = None) -> Frame:
        # So VideoOutputProxy can get this method
        fps, cum_durations = VideoOutput._get_fps_and_durations(self, fps)

        if fps == 0 and cum_durations:
            return Frame(bisect_right(cum_durations, time.total_seconds()))

        return Frame(cround(time.total_seconds() * fps) if fps > 0 else 0)

    def frame_to_time(self, frame: int, fps: VideoOutput | Fraction | None = None) -> Time:
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
