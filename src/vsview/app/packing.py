"""RGB packing implementations for VapourSynth to Qt conversion."""

from __future__ import annotations

import sys
from abc import ABC
from enum import Enum, IntEnum
from logging import Filter, LogRecord, getLogger
from typing import Any, Literal, assert_never, override

import vapoursynth
import vapoursynth as vs
from jetpytools import CustomValueError
from PySide6.QtGui import QImage
from vspackrgb.helpers import get_plane_buffer, packrgb

from .settings import SettingsManager

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated

logger = getLogger(__name__)


class FramePropsFilter(Filter):
    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.msgs = set[str]()

    @override
    def filter(self, record: LogRecord) -> bool | LogRecord:
        logged = super().filter(record)

        if logged and record.msg not in self.msgs:
            self.msgs.add(record.msg)
            return logged
        return False


logger.addFilter(FramePropsFilter(logger.name))


def select_in_matrix(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
    if f.format.color_family == vs.RGB and f.props.get("_Matrix", vs.MATRIX_UNSPECIFIED) == vs.MATRIX_UNSPECIFIED:
        f = f.copy()
        f.props["_Matrix"] = vs.MATRIX_RGB
    return f


def warn_missing_props(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
    specs: list[IntEnum] = [
        vs.MatrixCoefficients(f.props.get("_Matrix", 2)),
        vs.ColorPrimaries(f.props.get("_Primaries", 2)),
        vs.TransferCharacteristics(f.props.get("_Transfer", 2)),
    ]

    if unknowns := [spec for spec in specs if spec == 2]:
        prop_names = [e.name.split("_")[0].title() for e in unknowns]
        logger.warning("The following properties are missing: %r", prop_names)

    return f


class Packer(ABC):
    """RGB packer"""

    class FormatConfig(Enum):
        """Configuration for video formats."""

        INT8 = 8, vs.INTEGER
        INT10 = 10, vs.INTEGER
        INT16 = 16, vs.INTEGER
        FP16 = 16, vs.FLOAT
        FP32 = 32, vs.FLOAT

        @property
        def bitdepth(self) -> int:
            """The bit depth of the format."""
            return self.value[0]

        @property
        def sample_type(self) -> vs.SampleType:
            """The VapourSynth sample type."""
            return self.value[1]

        @property
        def vs(self) -> vs.PresetVideoFormat:
            """The RGB PresetVideoFormat corresponding to this format config."""
            return vs.PresetVideoFormat(vs.RGB << 28 | self.sample_type << 24 | self.bitdepth << 16)

        @property
        def vs_alpha(self) -> vapoursynth.PresetVideoFormat:
            """
            The alpha channel PresetVideoFormat (GRAY) corresponding to this format config.
            """
            return vs.PresetVideoFormat(vs.GRAY << 28 | self.sample_type << 24 | self.bitdepth << 16)

        @property
        def qt(self) -> QImage.Format:
            """The matching Qt QImage.Format without alpha support."""
            match self.value:
                case 8, _:
                    return QImage.Format.Format_RGB32
                case 10, _:
                    return QImage.Format.Format_RGB30
                case 16, vs.INTEGER:
                    return QImage.Format.Format_RGBA64
                case 16, vs.FLOAT:
                    return QImage.Format.Format_RGBA16FPx4
                case 32, _:
                    return QImage.Format.Format_RGBA32FPx4
                case _:
                    assert_never(self.value)

        @property
        def qt_alpha(self) -> QImage.Format:
            """The matching Qt QImage.Format with alpha channel support."""
            match self.value:
                case 8, _:
                    return QImage.Format.Format_ARGB32
                case 10, _:
                    return QImage.Format.Format_A2RGB30_Premultiplied
                case _:
                    return self.qt

        @property
        def formats(
            self,
        ) -> tuple[vapoursynth.PresetVideoFormat, vapoursynth.PresetVideoFormat, QImage.Format, QImage.Format]:
            """Returns a tuple containing (vs, vs_alpha, qt, qt_alpha)."""
            return self.vs, self.vs_alpha, self.qt, self.qt_alpha

    def __init__(
        self,
        bit_depth: int | None = None,
        sample_type: vs.SampleType = vs.INTEGER,
        *,
        hdr: bool = False,
    ) -> None:
        self.format = Packer.FormatConfig((bit_depth or SettingsManager.global_settings.view.bit_depth, sample_type))
        self.hdr = hdr

        if self.hdr and (self.format.bitdepth < 16 or self.format.sample_type != vs.FLOAT):
            raise CustomValueError("Invalid format for HDR")

    def to_rgb_planar(self, clip: vs.VideoNode, **kwargs: Any) -> vs.VideoNode:
        """Converts clip to planar RGB."""

        params = dict[str, Any](
            format=self.format.vs,
            dither_type=SettingsManager.global_settings.view.dither_type,
            resample_filter_uv=SettingsManager.global_settings.view.chroma_resizer.vs_func,
            filter_param_a_uv=SettingsManager.global_settings.view.chroma_resizer.param_a,
            filter_param_b_uv=SettingsManager.global_settings.view.chroma_resizer.param_b,
            transfer=vs.TRANSFER_BT709,
            primaries=vs.PRIMARIES_BT709,
        )

        if self.hdr:
            # For HDR, we must use BT.2020 PQ (ST2084)
            params["transfer"] = vs.TRANSFER_ST2084
            params["primaries"] = vs.PRIMARIES_BT2020
            return clip.resize.Point(**params | kwargs)

        # Returns directly the clip without checking anything.
        # If color specs are set, this will work.
        if (policy := SettingsManager.global_settings.view.props_policy) == "error":
            return clip.resize.Point(**params | kwargs)

        if policy == "warn":
            clip = clip.std.ModifyFrame(clip, warn_missing_props)

        # If the corresponding frameprop is set to a value other than unspecified,
        # the frameprop is used instead of this parameter
        in_params = dict[str, Any](transfer_in=vs.TRANSFER_BT709, primaries_in=vs.PRIMARIES_BT709)
        if clip.format.id == vs.PresetVideoFormat.NONE:
            clip = clip.std.ModifyFrame(clip, select_in_matrix)
        elif clip.format.color_family is vs.RGB:
            in_params["matrix_in"] = vs.MATRIX_RGB

        return clip.resize.Point(**params | in_params | kwargs)

    def to_rgb_packed(self, clip: vs.VideoNode, alpha: vs.VideoNode | Literal[True] | None = None) -> vs.VideoNode:
        """Converts planar VapourSynth RGB to interleaved/packed Qt format."""
        return packrgb(clip, alpha, "cython")

    def pack_clip(self, clip: vs.VideoNode, alpha: vs.VideoNode | Literal[True] | None = None) -> vs.VideoNode:
        """Converts a planar VideoNode and an optional alpha mask to a packed RGB/RGBA VideoNode."""
        if isinstance(alpha, vs.VideoNode):
            alpha = alpha.resize.Point(
                format=self.format.vs_alpha,
                dither_type=SettingsManager.global_settings.view.dither_type,
            )

        planar = self.to_rgb_planar(clip)
        packed = self.to_rgb_packed(planar, alpha)

        return packed.std.SetFrameProp("VSViewHasAlpha", True) if alpha else packed

    def frame_to_qimage(self, frame: vs.VideoFrame, **kwargs: Any) -> QImage:
        """
        Wraps a packed VapourSynth VideoFrame into a QImage.

        If the `copy_qimage` setting is enabled, ownership of the memory is transferred to Qt
        by returning a copy of the image.
        Otherwise, the returned QImage **does not own its memory** and points directly
        to the VapourSynth frame's buffer.

        !!! warning
            When `copy_qimage` is disabled, you MUST either keep the source `frame` alive as long
            as the QImage is used, or call ``.copy()`` on the returned QImage.
        """

        alpha = "VSViewHasAlpha" in frame.props or "_Alpha" in frame.props

        params = dict[str, Any](format=self.format.qt_alpha if alpha else self.format.qt) | kwargs

        # Handle the 4x width hack for (16/32)-bit packing
        width = frame.width
        if self.format.bitdepth >= 16:
            width //= 4

        # QImage supports Buffer inputs
        img = QImage(
            get_plane_buffer(frame, 0),  # type: ignore[call-overload]
            width,
            frame.height,
            frame.get_stride(0),
            params.pop("format"),
            **params,
        )

        # If we are in HDR/(16/32)-bit mode, we must copy the image
        if self.format.bitdepth >= 16 or SettingsManager.global_settings.view.copy_qimage:
            return img.copy()

        return img


@deprecated("Deprecated method. Use Packer.", category=DeprecationWarning)
def get_packer(method: str | None = None, bit_depth: int | None = None) -> Packer:
    return Packer(bit_depth)
