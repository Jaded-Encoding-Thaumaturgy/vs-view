from enum import StrEnum
from typing import Self, override


class Resizer(StrEnum):
    BILINEAR = "bilinear", "Bilinear", "bilinear"

    POINT = "point", "Point", "point"

    CATROM = "catrom", "Catrom (0, 0.5)", "bicubic", 0, 0.5
    MITCHELL = "mitchell", "Mitchell (1/3, 1/3)", "bicubic", 1 / 3, 1 / 3
    FFMPEG_BC = "ffmpeg_bc", "FFmpeg Bicubic (0, 0.6)", "bicubic", 0, 0.6
    ADOBE_BICUBIC = "adobe_bicubic", "Adobe Bicubic (0, 0.75)", "bicubic", 0, 0.75

    SPLINE16 = "spline16", "Spline16", "spline16"
    SPLINE36 = "spline36", "Spline36", "spline36"

    LANCZOS2 = "lanczos2", "Lanczos (2-taps)", "lanczos", 2
    LANCZOS3 = "lanczos3", "Lanczos (3-taps)", "lanczos", 3

    label: str
    vs_func: str
    param_a: float | None
    param_b: float | None

    def __new__(
        cls,
        value: str,
        label: str,
        vs_func: str,
        param_a: float | None = None,
        param_b: float | None = None,
    ) -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        obj.vs_func = vs_func
        obj.param_a = param_a
        obj.param_b = param_b
        return obj

    @override
    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        # Settings files written before the stable ids store the label
        return next((member for member in cls if member.label == value), None)
