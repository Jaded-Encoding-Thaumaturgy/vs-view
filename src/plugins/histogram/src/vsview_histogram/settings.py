from typing import Literal

from pydantic import BaseModel, Field


class LevelsSettings(BaseModel):
    factor: float = 100.0
    bin_res: Literal[0, 256, 512, 1024] = 0
    show_unsafe: bool = True


class WaveformSettings(BaseModel):
    mode: Literal["luma", "parade"] = "luma"
    res: Literal[0, 256, 512, 1024] = 0
    gain: float = 1.0
    dynamic_gain: bool = True
    show_zones: bool = True


class VectorscopeSettings(BaseModel):
    mode: Literal["density", "chroma_wheel", "pixel_color"] = "density"
    res: Literal[0, 256, 512, 1024] = 0
    luma: int = 192
    matrix: Literal["auto", "bt709", "bt601", "bt2020", "st240m"] = "auto"


class LumaSettings(BaseModel):
    shift: int = 4
    sawtooth: bool = False


class GlobalSettings(BaseModel):
    selected_tab: int = 0

    levels: LevelsSettings = Field(default_factory=LevelsSettings)
    waveform: WaveformSettings = Field(default_factory=WaveformSettings)
    vectorscope: VectorscopeSettings = Field(default_factory=VectorscopeSettings)
    luma: LumaSettings = Field(default_factory=LumaSettings)
