from enum import StrEnum
from typing import Self


class Resizer(StrEnum):
    BILINEAR = "Bilinear", "bilinear"

    POINT = "Point", "point"

    CATROM = "Catrom (0, 0.5)", "bicubic", 0, 0.5
    MITCHELL = "Mitchell (1/3, 1/3)", "bicubic", 1 / 3, 1 / 3
    FFMPEG_BC = "FFmpeg Bicubic (0, 0.6)", "bicubic", 0, 0.6
    ADOBE_BICUBIC = "Adobe Bicubic (0, 0.75)", "bicubic", 0, 0.75

    SPLINE16 = "Spline16", "spline16"
    SPLINE36 = "Spline36", "spline36"

    LANCZOS2 = "Lanczos (2-taps)", "lanczos", 2
    LANCZOS3 = "Lanczos (3-taps)", "lanczos", 3

    vs_func: str
    param_a: float | None
    param_b: float | None

    def __new__(cls, value: str, vs_func: str, param_a: float | None = None, param_b: float | None = None) -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.vs_func = vs_func
        obj.param_a = param_a
        obj.param_b = param_b
        return obj
