from __future__ import annotations

from collections.abc import Mapping
from logging import getLogger
from typing import TYPE_CHECKING, Any

from vapoursynth import AudioNode, SampleType, VideoOutputTuple

from ...env import getenv_bool
from ..packing import Packer
from .audio import AudioOutput
from .video import VideoOutput

if TYPE_CHECKING:
    from ..plugins import PluginAPI

logger = getLogger(__name__)


class OutputsManager:
    """Manages video and audio outputs."""

    def __init__(self, api: PluginAPI) -> None:
        self.api = api

    @property
    def voutputs(self) -> list[VideoOutput]:
        """List of video outputs."""
        return getattr(self, "_voutputs", [])

    @property
    def aoutputs(self) -> list[AudioOutput]:
        """List of audio outputs."""
        return getattr(self, "_aoutputs", [])

    @property
    def current_video_index(self) -> int:
        """Current video output index."""
        return getattr(self, "_current_video_index", 0)

    @current_video_index.setter
    def current_video_index(self, value: int) -> None:
        self._current_video_index = value

    @property
    def current_audio_index(self) -> int:
        """Current audio output index."""
        return getattr(self, "_current_audio_index", 0)

    @current_audio_index.setter
    def current_audio_index(self, value: int) -> None:
        self._current_audio_index = value

    @property
    def current_voutput(self) -> VideoOutput | None:
        """Current video output."""
        if self.voutputs:
            return self.voutputs[self.current_video_index]
        return None

    @property
    def current_aoutput(self) -> AudioOutput | None:
        """Current audio output."""
        return (
            self.aoutputs[self.current_audio_index]
            if self.current_audio_index in range(len(self.aoutputs))
            else None  # No format
        )

    def create_voutputs(
        self,
        content: Any,
        vs_vouputs: Mapping[int, VideoOutputTuple],
        metadata: dict[int, Any],
        *,
        last_frame: int = 0,
    ) -> list[VideoOutput]:
        """
        Create VideoOutput wrappers for all video outputs.

        Returns an empty list on error; caller is responsible for cleanup.
        """
        if not vs_vouputs:
            logger.error("No video outputs found")

        # Snapshot items to avoid keeping the dict iterator alive during processing.
        # This prevents the iterator from holding references to VS objects in the traceback.
        items = list(vs_vouputs.items())
        voutputs = list[VideoOutput]()

        try:
            for i, output in items:
                vmetadata = metadata.get(i)
                if getenv_bool("VSVIEW_HDR") and (vmetadata.hdr in [None, True] if vmetadata else True):
                    packer = Packer(16, SampleType.FLOAT, hdr=True)
                else:
                    packer = Packer()

                logger.debug(
                    "Configured video packer (%s-bit, %s)",
                    packer.format.bitdepth,
                    packer.format.sample_type.name,
                )

                voutput = VideoOutput(output, i, packer, vmetadata)
                voutput.last_frame = last_frame
                voutput.prepare_video(self.api)
                voutputs.append(voutput)
        except Exception:
            for voutput in voutputs:
                voutput.clear()
            logger.exception("Failed to load script: %r", content)
            return []

        self._voutputs = voutputs

        return voutputs

    def create_aoutputs(
        self,
        content: Any,
        vs_aouputs: Mapping[int, AudioNode],
        metadata: dict[int, Any],
        *,
        delay_s: float = 0.0,
    ) -> list[AudioOutput]:
        """Create AudioOutput wrappers for all audio outputs."""

        aoutputs = list[AudioOutput]()

        if not vs_aouputs:
            logger.debug("No audio outputs found")
            return []

        items = list(vs_aouputs.items())

        try:
            for i, output in items:
                aoutput = AudioOutput(output, i, metadata.get(i))
                aoutput.prepare_audio(delay_s, self.api)
                aoutputs.append(aoutput)
        except Exception:
            for aoutput in aoutputs:
                aoutput.clear()
            logger.exception("Failed to initialize aoutput: %r", content)
            return []

        self._aoutputs = aoutputs

        return aoutputs

    def clear(self) -> None:
        """Clear all outputs."""

        for output in self.voutputs:
            output.clear()
        self._voutputs = []

        for output in self.aoutputs:
            output.clear()
        self._aoutputs = []
