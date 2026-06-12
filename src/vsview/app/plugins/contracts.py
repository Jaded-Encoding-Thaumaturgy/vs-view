from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction
from typing import Any, Self

import vapoursynth as vs
from pydantic import BaseModel

from vsview.app.outputs import VideoOutput
from vsview.app.packing import Packer
from vsview.app.views import OutputInfo
from vsview.types import Frame, Time


@dataclass(frozen=True, slots=True)
class VideoOutputProxy:
    """Read-only proxy for a video output."""

    vs_index: int
    """Index of the video output in the VapourSynth environment."""

    vs_name: str = field(hash=False, compare=False)
    """Name of the video output."""

    vs_output: vs.VideoOutputTuple = field(hash=False, compare=False)
    """The object created by `vapoursynth.get_outputs()`."""

    props: Mapping[int, Mapping[str, Any]] = field(hash=False, compare=False)
    """Frame properties of the clip."""

    framedurs: Sequence[float] | None = field(hash=False, compare=False)
    """Frame durations of the clip."""

    cum_durations: Sequence[float] | None = field(hash=False, compare=False)
    """Cumulative durations of the clip."""

    kwargs: Mapping[str, Any] = field(hash=False, compare=False)
    """Additional metadata provided by the user via `set_output()`."""

    info: OutputInfo = field(hash=False, compare=False)
    """Output information."""

    packer: Packer = field(hash=False, compare=False)
    """The packer used by this VideoOutput."""

    @property
    def packer_sdr(self) -> Packer:
        """
        Same as `packer` if this output is not HDR capable,
        otherwise returns a 10-bit Packer with `hdr=False` to ensure SDR processing.
        """
        return self.packer if not self.packer.hdr else Packer(10, vs.SampleType.INTEGER)

    def time_to_frame(self, time: timedelta, fps: VideoOutputProxy | Fraction | None = None) -> Frame:
        """Convert a time to a frame number for this output."""
        return VideoOutput.time_to_frame(self, time, fps)  # type: ignore[arg-type]

    def frame_to_time(self, frame: int, fps: VideoOutputProxy | Fraction | None = None) -> Time:
        """Convert a frame number to time for this output."""
        return VideoOutput.frame_to_time(self, frame, fps)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AudioOutputProxy:
    """Read-only proxy for an audio output."""

    vs_index: int
    """Index of the audio output in the VapourSynth environment."""

    vs_name: str = field(hash=False, compare=False)
    """Name of the audio output."""

    vs_output: vs.AudioNode = field(hash=False, compare=False)
    """The object created by `vapoursynth.get_outputs()`."""

    kwargs: Mapping[str, Any] = field(hash=False, compare=False)
    """Additional metadata provided by the user via `set_output()`."""


class LocalSettingsModel(BaseModel):
    """
    Base class for settings with optional local overrides.

    Fields set to `None` fall back to the corresponding global value.
    """

    def resolve(self, global_settings: BaseModel) -> Self:
        """Resolve global settings with local overrides applied."""
        base_values = global_settings.model_dump(include=set(self.__class__.model_fields))
        overrides = self.model_dump(exclude_none=True)

        return self.__class__(**base_values | overrides)
