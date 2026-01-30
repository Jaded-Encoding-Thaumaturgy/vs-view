from __future__ import annotations

from collections.abc import Mapping
from logging import getLogger
from typing import TYPE_CHECKING, Any

from vapoursynth import AudioNode, VideoOutputTuple

from .audio import AudioOutput
from .packing import Packer, get_packer
from .video import VideoOutput

if TYPE_CHECKING:
    from ..plugins import PluginAPI

logger = getLogger(__name__)


class OutputsManager:
    """Manages video and audio outputs."""

    def __init__(self, api: PluginAPI) -> None:
        self.api = api

    @property
    def packer(self) -> Packer:
        return self._packer

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
    def current_audio_index(self) -> int | None:
        """Current audio output index."""
        return getattr(self, "_current_audio_index", None)

    @current_audio_index.setter
    def current_audio_index(self, value: int | None) -> None:
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
        if self.current_audio_index is not None and self.aoutputs:
            return self.aoutputs[self.current_audio_index]
        return None

    def create_voutputs(
        self,
        content: Any,
        vs_vouputs: Mapping[int, VideoOutputTuple],
        metadata: dict[int, Any],
    ) -> list[VideoOutput]:
        """
        Create VideoOutput wrappers for all video outputs.

        Returns an empty list on error; caller is responsible for cleanup.
        """

        voutputs = list[VideoOutput]()
        self._packer = get_packer()

        logger.debug("Configured video packer: %s (%s-bit)", self.packer.name, self.packer.bit_depth)

        if not vs_vouputs:
            logger.error("No video outputs found")

        # Snapshot items to avoid keeping the dict iterator alive during processing.
        # This prevents the iterator from holding references to VS objects in the traceback.
        items = list(vs_vouputs.items())

        try:
            for i, output in items:
                voutputs.append(VideoOutput(output, i, self.packer, metadata.get(i)))
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

        for output in self.aoutputs:
            output.clear()
