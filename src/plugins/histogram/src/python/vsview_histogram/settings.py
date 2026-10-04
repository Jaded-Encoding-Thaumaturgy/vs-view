from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field


class CieMode(IntEnum):
    CIE_1931 = 0
    CIE_1976 = 1


class RenderMode(IntEnum):
    DENSITY = 0
    CHROMA_WHEEL = 1
    PIXEL_COLOR = 2


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
    mode: RenderMode = RenderMode.DENSITY
    res: Literal[0, 256, 512, 1024] = 0
    luma: float = 1.5
    matrix: Literal["auto", "bt709", "bt601", "bt2020", "st240m"] = "auto"


class LumaSettings(BaseModel):
    shift: int = 4
    sawtooth: bool = False


class CIEDiagramSettings(BaseModel):
    mode: CieMode = CieMode.CIE_1931
    render_mode: RenderMode = RenderMode.PIXEL_COLOR
    res: Literal[0, 256, 512, 1024] = 0
    show_rec709: bool = True
    show_rec601: bool = False
    show_dcip3: bool = True
    show_rec2020: bool = True
    luma: float = 1.0


class GlobalSettings(BaseModel):
    selected_tab: int = 0

    levels: LevelsSettings = Field(default_factory=LevelsSettings)
    waveform: WaveformSettings = Field(default_factory=WaveformSettings)
    vectorscope: VectorscopeSettings = Field(default_factory=VectorscopeSettings)
    luma: LumaSettings = Field(default_factory=LumaSettings)
    cie: CIEDiagramSettings = Field(default_factory=CIEDiagramSettings)
