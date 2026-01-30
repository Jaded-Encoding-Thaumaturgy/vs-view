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


class Packer(ABC):
    """Abstract base class for RGB packers."""

    FORMAT_CONFIG: Mapping[int, tuple[vs.PresetVideoFormat, QImage.Format]] = {
        8: (vs.RGB24, QImage.Format.Format_RGB32),
        10: (vs.RGB30, QImage.Format.Format_RGB30),
    }

    name: ClassVar[str]

    def __init__(self, bit_depth: int) -> None:
        self.bit_depth = bit_depth
        self.vs_format, self.qt_format = Packer.FORMAT_CONFIG[bit_depth]

    def to_rgb_planar(self, clip: vs.VideoNode, **kwargs: Any) -> vs.VideoNode:
        params = dict[str, Any](
            format=self.vs_format,
            dither_type=SettingsManager.global_settings.view.dither_type,
            resample_filter_uv=SettingsManager.global_settings.view.chroma_resizer.vs_func,
            filter_param_a_uv=SettingsManager.global_settings.view.chroma_resizer.param_a,
            filter_param_b_uv=SettingsManager.global_settings.view.chroma_resizer.param_b,
        )

        return clip.resize.Point(**params | kwargs)

    @abstractmethod
    def to_rgb_packed(self, clip: vs.VideoNode) -> vs.VideoNode: ...

    def pack_clip(self, clip: vs.VideoNode) -> vs.VideoNode:
        planar = self.to_rgb_planar(clip)
        packed = self.to_rgb_packed(planar)
        return packed

    def frame_to_qimage(self, frame: vs.VideoFrame) -> QImage:
        # QImage supports Buffer inputs
        return QImage(
            get_plane_buffer(frame, 0),  # type: ignore[call-overload]
            frame.width,
            frame.height,
            frame.get_stride(0),
            self.qt_format,
        )


class VszipPacker(Packer):
    name = "vszip"

    def to_rgb_packed(self, clip: vs.VideoNode) -> vs.VideoNode:
        return clip.vszip.PackRGB()


class CythonPacker(Packer):
    name = "cython"

    def to_rgb_packed(self, clip: vs.VideoNode) -> vs.VideoNode:
        return packrgb(clip, backend="cython")


class NumpyPacker(Packer):
    name = "numpy"

    def to_rgb_packed(self, clip: vs.VideoNode) -> vs.VideoNode:
        return packrgb(clip, backend="numpy")


class PythonPacker(Packer):
    name = "python"

    def to_rgb_packed(self, clip: vs.VideoNode) -> vs.VideoNode:
        return packrgb(clip, backend="python")


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
                logger.error("vszip plugin is not available")
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
