from __future__ import annotations

import ctypes
import logging

import pytest
import vapoursynth as vs
from jetpytools import CustomValueError
from PySide6.QtGui import QColorSpace, QImage

from vsview.app.packing import FramePropsFilter, Packer, select_in_matrix, warn_missing_props
from vsview.app.settings import SettingsManager

core = vs.core


def test_frame_props_filter() -> None:
    filter_obj = FramePropsFilter("test_filter")
    assert filter_obj.msgs == set()

    record1 = logging.LogRecord("test_filter", logging.WARNING, "file.py", 10, "Missing props", (), None)
    record2 = logging.LogRecord("test_filter", logging.WARNING, "file.py", 11, "Missing props", (), None)
    record3 = logging.LogRecord("test_filter", logging.WARNING, "file.py", 12, "Other warning", (), None)

    # First encounter of msg should be logged
    assert filter_obj.filter(record1) is not False
    assert "Missing props" in filter_obj.msgs

    # Duplicate msg should be filtered out (return False)
    assert filter_obj.filter(record2) is False

    # New message should be logged
    assert filter_obj.filter(record3) is not False
    assert "Other warning" in filter_obj.msgs

    # Test super().filter failure (different logger name filter)
    strict_filter = FramePropsFilter("specific_name")
    unmatched_record = logging.LogRecord("other_name", logging.INFO, "f.py", 1, "Msg", (), None)
    assert strict_filter.filter(unmatched_record) is False


@pytest.mark.vpy("initial-core")
def test_select_in_matrix() -> None:
    # RGB frame without _Matrix prop
    rgb_clip = core.std.BlankClip(format=vs.RGB24, width=16, height=16, length=1)
    frame_rgb = rgb_clip.get_frame(0)
    assert "_Matrix" not in frame_rgb.props
    processed_rgb = select_in_matrix(0, frame_rgb)
    assert processed_rgb.props["_Matrix"] == vs.MATRIX_RGB

    # RGB frame with existing _Matrix prop
    rgb_clip_matrix = core.std.SetFrameProp(rgb_clip, prop="_Matrix", intval=vs.MATRIX_BT709)
    frame_rgb_matrix = rgb_clip_matrix.get_frame(0)
    processed_rgb_matrix = select_in_matrix(0, frame_rgb_matrix)
    assert processed_rgb_matrix.props["_Matrix"] == vs.MATRIX_BT709

    # Non-RGB frame (YUV)
    yuv_clip = core.std.BlankClip(format=vs.YUV420P8, width=16, height=16, length=1)
    frame_yuv = yuv_clip.get_frame(0)
    processed_yuv = select_in_matrix(0, frame_yuv)
    assert processed_yuv.props.get("_Matrix", vs.MATRIX_UNSPECIFIED) == vs.MATRIX_UNSPECIFIED


@pytest.mark.vpy("initial-core")
def test_warn_missing_props(caplog: pytest.LogCaptureFixture) -> None:
    clip = core.std.BlankClip(format=vs.YUV420P8, width=16, height=16, length=1)

    # Frame missing matrix, primaries, transfer (defaults to 2 / UNSPECIFIED)
    frame_missing = clip.get_frame(0)
    with caplog.at_level(logging.WARNING):
        result_frame = warn_missing_props(0, frame_missing)
        assert result_frame is frame_missing
        assert "The following properties are missing:" in caplog.text
        assert "Matrix" in caplog.text
        assert "Primaries" in caplog.text
        assert "Transfer" in caplog.text

    # Frame with all properties explicitly specified (not equal to 2)
    clip_full = core.std.SetFrameProps(
        clip,
        _Matrix=vs.MATRIX_BT709,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )
    frame_full = clip_full.get_frame(0)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        warn_missing_props(0, frame_full)
        assert caplog.text == ""


def test_packer_format_config_enum() -> None:
    fmt_int8 = Packer.FormatConfig.INT8
    assert fmt_int8.bitdepth == 8
    assert fmt_int8.sample_type == vs.INTEGER
    assert fmt_int8.vs == vs.PresetVideoFormat.RGB24
    assert fmt_int8.vs_alpha == vs.PresetVideoFormat.GRAY8
    assert fmt_int8.qt == QImage.Format.Format_RGB32
    assert fmt_int8.qt_alpha == QImage.Format.Format_ARGB32
    assert fmt_int8.formats == (
        vs.PresetVideoFormat.RGB24,
        vs.PresetVideoFormat.GRAY8,
        QImage.Format.Format_RGB32,
        QImage.Format.Format_ARGB32,
    )

    fmt_int10 = Packer.FormatConfig.INT10
    assert fmt_int10.bitdepth == 10
    assert fmt_int10.sample_type == vs.INTEGER
    assert fmt_int10.qt == QImage.Format.Format_RGB30
    assert fmt_int10.qt_alpha == QImage.Format.Format_A2RGB30_Premultiplied

    fmt_int16 = Packer.FormatConfig.INT16
    assert fmt_int16.bitdepth == 16
    assert fmt_int16.sample_type == vs.INTEGER
    assert fmt_int16.qt == QImage.Format.Format_RGBA64
    assert fmt_int16.qt_alpha == QImage.Format.Format_RGBA64

    fmt_fp16 = Packer.FormatConfig.FP16
    assert fmt_fp16.bitdepth == 16
    assert fmt_fp16.sample_type == vs.FLOAT
    assert fmt_fp16.qt == QImage.Format.Format_RGBA16FPx4
    assert fmt_fp16.qt_alpha == QImage.Format.Format_RGBA16FPx4

    fmt_fp32 = Packer.FormatConfig.FP32
    assert fmt_fp32.bitdepth == 32
    assert fmt_fp32.sample_type == vs.FLOAT
    assert fmt_fp32.qt == QImage.Format.Format_RGBA32FPx4
    assert fmt_fp32.qt_alpha == QImage.Format.Format_RGBA32FPx4


def test_packer_init_and_hdr_validation() -> None:
    # Standard packer initialization with default bit depth
    packer = Packer()
    assert packer.format == Packer.FormatConfig.INT8
    assert not packer.hdr

    # Explicit bit depth and sample type
    packer10 = Packer(10, vs.INTEGER)
    assert packer10.format == Packer.FormatConfig.INT10

    # Valid HDR initialization (FP16 or FP32)
    packer_hdr = Packer(16, vs.FLOAT, hdr=True)
    assert packer_hdr.hdr
    assert packer_hdr.format == Packer.FormatConfig.FP16

    packer_hdr32 = Packer(32, vs.FLOAT, hdr=True)
    assert packer_hdr32.hdr
    assert packer_hdr32.format == Packer.FormatConfig.FP32

    # Invalid HDR initializations: integer sample type or bit depth < 16
    with pytest.raises(CustomValueError, match="Invalid format for HDR"):
        Packer(8, vs.INTEGER, hdr=True)

    with pytest.raises(CustomValueError, match="Invalid format for HDR"):
        Packer(16, vs.INTEGER, hdr=True)


@pytest.mark.vpy("initial-core")
def test_packer_to_rgb_planar() -> None:
    yuv_clip = core.std.BlankClip(format=vs.YUV420P8, width=32, height=32, length=1)
    yuv_clip = core.std.SetFrameProps(
        yuv_clip,
        _Matrix=vs.MATRIX_BT709,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )
    rgb_clip = core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=1)
    rgb_clip = core.std.SetFrameProps(
        rgb_clip,
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )

    packer = Packer(8, vs.INTEGER)

    # Standard conversion (props_policy default / ignore)
    planar = packer.to_rgb_planar(yuv_clip)
    assert planar.format.id == vs.PresetVideoFormat.RGB24
    assert planar.width == 32
    assert planar.height == 32

    # HDR mode conversion
    packer_hdr = Packer(16, vs.FLOAT, hdr=True)
    hdr_planar = packer_hdr.to_rgb_planar(yuv_clip)
    assert hdr_planar.format.sample_type == vs.FLOAT
    assert hdr_planar.format.bits_per_sample == 16

    # Policy == "error"
    SettingsManager.global_settings.view.props_policy = "error"
    planar_error = packer.to_rgb_planar(yuv_clip)
    assert planar_error.format.id == vs.PresetVideoFormat.RGB24

    # Policy == "warn"
    SettingsManager.global_settings.view.props_policy = "warn"
    planar_warn = packer.to_rgb_planar(yuv_clip)
    assert planar_warn.format.id == vs.PresetVideoFormat.RGB24

    # RGB input clip
    SettingsManager.global_settings.view.props_policy = "ignore"
    planar_rgb = packer.to_rgb_planar(rgb_clip)
    assert planar_rgb.format.id == vs.PresetVideoFormat.RGB24

    # Variable format clip
    rgb1 = core.std.SetFrameProps(
        core.std.BlankClip(format=vs.RGB24, width=32, height=32, length=1),
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )
    rgb2 = core.std.SetFrameProps(
        core.std.BlankClip(format=vs.RGB27, width=32, height=32, length=1),
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )
    var_clip = core.std.Splice([rgb1, rgb2], mismatch=True)
    assert var_clip.format.id == vs.PresetVideoFormat.NONE
    planar_var = packer.to_rgb_planar(var_clip)
    assert planar_var.width == 32
    assert planar_var.height == 32


@pytest.mark.vpy("initial-core")
def test_packer_to_rgb_packed_and_pack_clip() -> None:
    clip = core.std.BlankClip(format=vs.YUV420P8, width=32, height=32, length=1)
    clip = core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_BT709, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )
    alpha = core.std.BlankClip(format=vs.GRAY8, width=32, height=32, length=1)

    packer = Packer(8, vs.INTEGER)

    # to_rgb_packed directly
    planar = packer.to_rgb_planar(clip)
    packed_node = packer.to_rgb_packed(planar)
    assert packed_node.width == 32
    assert packed_node.height == 32

    # pack_clip without alpha
    packed_no_alpha = packer.pack_clip(clip)
    frame_no_alpha = packed_no_alpha.get_frame(0)
    assert "VSViewHasAlpha" not in frame_no_alpha.props

    # pack_clip with VideoNode alpha (tests alpha resizing branch: isinstance(alpha, vs.VideoNode))
    packed_with_alpha = packer.pack_clip(clip, alpha=alpha)
    frame_with_alpha = packed_with_alpha.get_frame(0)
    assert bool(frame_with_alpha.props.get("VSViewHasAlpha")) is True


@pytest.mark.vpy("initial-core")
def test_packer_frame_to_qimage_8bit() -> None:
    clip = core.std.BlankClip(
        format=vs.RGB24,
        width=64,
        height=48,
        length=1,
    ).std.SetFrameProps(
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )
    packer = Packer(8, vs.INTEGER)

    packed_clip = packer.pack_clip(clip)
    frame = packed_clip.get_frame(0)

    frame_ptr = frame.get_read_ptr(0).value

    # 8-bit frame conversion to QImage without copy_qimage (shares buffer memory)
    SettingsManager.global_settings.view.copy_qimage = False
    qimg = packer.frame_to_qimage(frame)

    assert isinstance(qimg, QImage)
    assert not qimg.isNull()
    assert qimg.width() == 64
    assert qimg.height() == 48
    assert qimg.format() == QImage.Format.Format_RGB32
    assert qimg.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    # Memory ownership check: buffer pointer matches VapourSynth frame read pointer directly
    assert ctypes.addressof(ctypes.c_char.from_buffer(qimg.bits())) == frame_ptr

    # With copy_qimage = True (allocates independent memory copy)
    SettingsManager.global_settings.view.copy_qimage = True
    qimg_copy = packer.frame_to_qimage(frame)
    assert not qimg_copy.isNull()
    assert qimg_copy.width() == 64
    assert qimg_copy.height() == 48
    assert qimg_copy.format() == QImage.Format.Format_RGB32
    # Memory ownership check: copied buffer pointer is separate from VapourSynth frame read pointer
    assert ctypes.addressof(ctypes.c_char.from_buffer(qimg_copy.bits())) != frame_ptr


@pytest.mark.vpy("initial-core")
def test_packer_frame_to_qimage_alpha_and_colorspaces() -> None:
    base_clip = core.std.BlankClip(
        format=vs.RGB24,
        width=64,
        height=48,
        length=1,
    ).std.SetFrameProps(
        _Matrix=vs.MATRIX_RGB,
        _Primaries=vs.PRIMARIES_BT709,
        _Transfer=vs.TRANSFER_BT709,
    )

    alpha_node = base_clip.std.BlankClip(format=vs.GRAY8)
    packer = Packer(8, vs.INTEGER)

    # Test alpha detection via VSViewHasAlpha prop
    packed_alpha = packer.pack_clip(base_clip, alpha=alpha_node)
    frame_alpha = packed_alpha.get_frame(0)
    qimg_alpha = packer.frame_to_qimage(frame_alpha)
    assert not qimg_alpha.isNull()
    assert qimg_alpha.format() == QImage.Format.Format_ARGB32

    # Test when _Alpha prop is present on frame
    base_clip_with_alpha = base_clip.std.ClipToProp(alpha_node, "_Alpha")
    packed = packer.pack_clip(base_clip_with_alpha, alpha=True)
    qimg_alpha_prop = packer.frame_to_qimage(packed.get_frame(0))
    assert not qimg_alpha_prop.isNull()
    assert qimg_alpha_prop.format() == QImage.Format.Format_ARGB32

    # Test color space mappings by updating frame props on packed clip
    # BT2020 + ST2084 -> Bt2100Pq
    clip_pq = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_BT2020, _Transfer=vs.TRANSFER_ST2084)
    qimg_pq = packer.frame_to_qimage(clip_pq.get_frame(0))
    assert qimg_pq.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.Bt2100Pq)

    # BT2020 + ARIB_B67 -> Bt2100Hlg
    clip_hlg = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_BT2020, _Transfer=vs.TRANSFER_ARIB_B67)
    qimg_hlg = packer.frame_to_qimage(clip_hlg.get_frame(0))
    assert qimg_hlg.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.Bt2100Hlg)

    # BT2020 + BT709 transfer -> Bt2020
    clip_bt2020 = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_BT2020, _Transfer=vs.TRANSFER_BT709)
    qimg_bt2020 = packer.frame_to_qimage(clip_bt2020.get_frame(0))
    assert qimg_bt2020.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.Bt2020)

    # Display P3 (ST431_2)
    clip_p3_1 = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_ST431_2, _Transfer=vs.TRANSFER_BT709)
    qimg_p3_1 = packer.frame_to_qimage(clip_p3_1.get_frame(0))
    assert qimg_p3_1.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.DisplayP3)

    # Display P3 (ST432_1)
    clip_p3_2 = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_ST432_1, _Transfer=vs.TRANSFER_BT709)
    qimg_p3_2 = packer.frame_to_qimage(clip_p3_2.get_frame(0))
    assert qimg_p3_2.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.DisplayP3)

    # BT709 + TRANSFER_LINEAR -> SRgbLinear
    clip_linear = packed.std.SetFrameProps(_Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_LINEAR)
    qimg_linear = packer.frame_to_qimage(clip_linear.get_frame(0))
    assert qimg_linear.colorSpace() == QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear)


@pytest.mark.vpy("initial-core")
def test_packer_frame_to_qimage_16bit_width_division() -> None:
    clip = core.std.BlankClip(format=vs.YUV420P8, width=64, height=48, length=1)
    clip = core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_BT709, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )

    packer_fp16 = Packer(16, vs.FLOAT)
    packed = packer_fp16.pack_clip(clip)
    frame = packed.get_frame(0)

    # Bit depth >= 16 causes width division by 4 inside frame_to_qimage and forces a memory copy
    qimg = packer_fp16.frame_to_qimage(frame)
    assert not qimg.isNull()
    assert qimg.width() == 64
    assert qimg.height() == 48
    assert qimg.format() == QImage.Format.Format_RGBA16FPx4
    # Memory ownership check: bitdepth >= 16 forces a copy, so QImage buffer address is separate
    assert ctypes.addressof(ctypes.c_char.from_buffer(qimg.bits())) != frame.get_read_ptr(0).value
