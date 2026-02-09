"""RGB packing implementations for VapourSynth to Qt conversion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from functools import cache
from logging import getLogger
from typing import Any, ClassVar

import vapoursynth as vs
from PySide6.QtGui import QImage
from vspackrgb.helpers import get_plane_buffer, packrgb

from ...vsenv import create_environment
from ..settings import SettingsManager

logger = getLogger(__name__)


class AlphaNotImplementedError(NotImplementedError):
    """Alpha packing hasn't been implemented for this packer"""

    packer: Packer

    def __init__(self, packer: Packer) -> None:
        super().__init__(f"The packer '{packer.__class__.__name__}' can't pack clip with alpha plane")
        self.packer = packer


class Packer(ABC):
    """Abstract base class for RGB packers."""

    FORMAT_CONFIG: Mapping[int, tuple[vs.PresetVideoFormat, vs.PresetVideoFormat, QImage.Format, QImage.Format]] = {
        8: (vs.RGB24, vs.GRAY8, QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32),
        10: (vs.RGB30, vs.GRAY10, QImage.Format.Format_RGB30, QImage.Format.Format_A2RGB30_Premultiplied),
    }

    name: ClassVar[str]

    def __init__(self, bit_depth: int) -> None:
        self.bit_depth = bit_depth
        self.vs_format, self.vs_aformat, self.qt_format, self.qt_aformat = Packer.FORMAT_CONFIG[bit_depth]

    def to_rgb_planar(self, clip: vs.VideoNode, **kwargs: Any) -> vs.VideoNode:
        """Converts clip to planar vs.RGB24 or vs.RGB30."""
        params = dict[str, Any](
            format=self.vs_format,
            dither_type=SettingsManager.global_settings.view.dither_type,
            resample_filter_uv=SettingsManager.global_settings.view.chroma_resizer.vs_func,
            filter_param_a_uv=SettingsManager.global_settings.view.chroma_resizer.param_a,
            filter_param_b_uv=SettingsManager.global_settings.view.chroma_resizer.param_b,
        )

        return clip.resize.Point(**params | kwargs)

    @abstractmethod
    def to_rgb_packed(self, clip: vs.VideoNode, alpha: vs.VideoNode | None = None) -> vs.VideoNode:
        """Converts planar vs.RGB24 or vs.RGB30 to interleaved BGRA32 or RGB30 to packed A2R10G10B10"""

    def pack_clip(self, clip: vs.VideoNode, alpha: vs.VideoNode | None = None) -> vs.VideoNode:
        if alpha is not None:
            alpha = alpha.resize.Point(
                format=self.vs_aformat,
                dither_type=SettingsManager.global_settings.view.dither_type,
            )

        planar = self.to_rgb_planar(clip)
        packed = self.to_rgb_packed(planar, alpha)

        return packed.std.SetFrameProp("VSViewHasAlpha", True) if alpha else packed

    def frame_to_qimage(self, frame: vs.VideoFrame) -> QImage:
        # QImage supports Buffer inputs
        return QImage(
            get_plane_buffer(frame, 0),  # type: ignore[call-overload]
            frame.width,
            frame.height,
            frame.get_stride(0),
            self.qt_aformat if frame.props.get("VSViewHasAlpha") else self.qt_format,
        )


class VszipPacker(Packer):
    name = "vszip"

    def to_rgb_packed(self, clip: vs.VideoNode, alpha: vs.VideoNode | None = None) -> vs.VideoNode:
        if alpha:
            raise AlphaNotImplementedError(self)

        return clip.vszip.PackRGB()


class VSPackRGB(Packer):
    def to_rgb_packed(self, clip: vs.VideoNode, alpha: vs.VideoNode | None = None) -> vs.VideoNode:
        return packrgb(clip, alpha, self.name)  # type: ignore[arg-type]


class CythonPacker(VSPackRGB):
    name = "cython"


class NumpyPacker(VSPackRGB):
    name = "numpy"


class PythonPacker(VSPackRGB):
    name = "python"


@cache
def _is_vszip_available() -> bool:
    with create_environment(set_logger=False) as env, env.use():
        res = hasattr(env.core, "vszip") and hasattr(env.core.vszip, "PackRGB")
    return res


def get_packer(method: str | None = None, bit_depth: int | None = None) -> Packer:
    method = method or SettingsManager.global_settings.view.packing_method
    bit_depth = bit_depth or SettingsManager.global_settings.view.bit_depth

    if method == "auto":
        method = "vszip" if _is_vszip_available() else "cython"
        logger.debug("Auto-selected packing method: %s", method)

    match method:
        case "vszip":
            if not _is_vszip_available():
                logger.warning("vszip plugin is not available, falling back to Cython (8-bit) packer")
                return CythonPacker(8)

            return VszipPacker(bit_depth)

        case "cython":
            return CythonPacker(bit_depth)

        case "numpy":
            return NumpyPacker(bit_depth)

        case "python":
            return PythonPacker(bit_depth)

        case _:
            raise NotImplementedError
