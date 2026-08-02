from __future__ import annotations

from logging import getLogger
from typing import Annotated, override

from pydantic import BaseModel
from vapoursynth import AudioNode, SampleType

from vsview.api import Dropdown, NodeProcessor, Spin

logger = getLogger(__name__)


class GlobalSettings(BaseModel):
    bits: Annotated[
        int,
        Dropdown(
            label="Bitdepth",
            items=[(str(i), i) for i in [16, 24, 32]],
            tooltip=(
                "Output bits per sample.\n"  # no fmt
                "Integer output accepts 16 to 32 bits and float output only accepts 32 bits."
            ),
        ),
    ] = 32
    sample_type: Annotated[
        SampleType,
        Dropdown(
            label="Sample type",
            items=[(s.name.title(), s) for s in SampleType],
            tooltip="The sample type to convert to.",
        ),
    ] = SampleType.FLOAT
    sample_rate: Annotated[
        int | None,
        Spin(
            label="Sample rate",
            min=0,
            max=1_000_000,
            suffix=" Hz",
            min_text="None",
            tooltip="Target sample rate to convert to.\n0 means no resampling is performed.",
            to_ui=lambda v: 0 if v is None else v,
            from_ui=lambda v: None if v <= 0 else v,
        ),
    ] = None
    dither_type: Annotated[
        str | None,
        Dropdown(
            label="Dither Type",
            items=[
                ("None", "none"),
                ("Rectangular", "rectangular"),
                ("Triangular", "triangular"),
            ],
            tooltip="The type of dither applied when converting to an integer format. Default is 'triangular'.",
        ),
    ] = "triangular"


class AudioConvert(NodeProcessor[AudioNode, GlobalSettings]):
    identifier = "jet_vsview_audioconvert"
    display_name = "Audio Convert"

    @override
    def prepare(self, audio: AudioNode) -> AudioNode:
        logger.debug(
            "Using std.AudioResample on audio %r (%s Hz %s %s %r)",
            audio,
            self.settings.global_.sample_rate,
            self.settings.global_.sample_type.name,
            self.settings.global_.bits,
            self.settings.global_.dither_type,
        )
        return audio.std.AudioResample(
            samplerate=self.settings.global_.sample_rate,
            sampletype=self.settings.global_.sample_type,
            bits=self.settings.global_.bits,
            dither_type=self.settings.global_.dither_type,
        )
