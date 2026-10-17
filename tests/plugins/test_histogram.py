from __future__ import annotations

from collections.abc import Callable, Generator

import numpy as np
import pytest
import vapoursynth as vs
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment
from vsview_histogram import funcs
from vsview_histogram.plugin import HistogramPlugin
from vsview_histogram.settings import CieMode, RenderMode
from vsview_histogram.utils import CustomQImage

from tests.harness import HarnessPluginController
from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


@pytest.fixture(autouse=True)
def qt_event_loop(qt_event_loop: QtEventLoop) -> QtEventLoop:
    return qt_event_loop


def make_video_output(
    fmt: vs.PresetVideoFormat = vs.RGB24,
    *,
    matrix: int | None = None,
    primaries: int = vs.PRIMARIES_BT709,
    transfer: int = vs.TRANSFER_BT709,
    width: int = 64,
    height: int = 64,
    num_frames: int = 10,
) -> VideoOutput:
    """Helper to construct a fully prepared VideoOutput with specific format and frame properties."""
    if matrix is None:
        matrix = vs.MATRIX_RGB if fmt in (vs.RGB24, vs.RGB27, vs.RGB30, vs.RGB48, vs.RGBS, vs.RGBH) else vs.MATRIX_BT709

    clip = vs.core.std.BlankClip(
        length=num_frames,
        width=width,
        height=height,
        format=fmt,
        fpsnum=24,
        fpsden=1,
    )
    clip = vs.core.std.SetFrameProps(clip, _Matrix=matrix, _Primaries=primaries, _Transfer=transfer)
    packer = Packer()
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, packer)
    if primaries == vs.PRIMARIES_UNSPECIFIED or transfer == vs.TRANSFER_UNSPECIFIED:
        dummy = vs.core.std.BlankClip(format=vs.RGB24, length=num_frames, width=width, height=height)
        dummy = vs.core.std.SetFrameProps(
            dummy, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
        )
        voutput.prepared_clip = packer.pack_clip(dummy, None)
    else:
        voutput.prepared_clip = packer.pack_clip(clip, None)
    return voutput


@pytest.fixture
def populated_controller(
    plugin_controller: HarnessPluginController, vpy_env_factory: Callable[[], ManagedEnvironment]
) -> Generator[HarnessPluginController]:
    """Fixture providing a HarnessPluginController loaded with a valid RGB24 test VideoOutput."""
    plugin_controller.env = vpy_env_factory()

    with plugin_controller.env.use():
        voutput = make_video_output(vs.RGB24)
        plugin_controller.set_current_voutput(voutput)
        yield plugin_controller


# Plugin Lifecycle & Widget Hierarchy
def test_histogram_plugin_instantiation_and_tabs(populated_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange & Act
    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Assert
    assert plugin.tab_widget.count() == 5
    expected_titles = ["Levels", "Luma", "Vectorscope", "Waveform", "CIE Chromaticity"]
    actual_titles = [plugin.tab_widget.tabText(i) for i in range(5)]
    assert actual_titles == expected_titles

    # Verify initial tab matches global settings default
    assert plugin.tab_widget.currentIndex() == plugin.settings.global_.selected_tab

    # Verify destroy callback registration
    populated_controller.destroy()
    assert len(plugin.cie_nodes) == 0


# Levels Tab Across Formats
@pytest.mark.parametrize("preset_format", [vs.RGB24, vs.YUV420P8, vs.YUV420P10, vs.GRAY8, vs.RGBS])
def test_levels_tab_across_formats(
    populated_controller: HarnessPluginController, qtbot: QtBot, preset_format: vs.PresetVideoFormat
) -> None:
    # Arrange
    voutput = make_video_output(preset_format)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act: Select Levels tab and trigger update
    plugin.tab_widget.setCurrentIndex(0)
    plugin.update_histogram(0).result()

    # Assert controls update settings
    plugin.levels_unsafe_checkbox.setChecked(False)
    assert plugin.settings.global_.levels.show_unsafe is False

    plugin.levels_bin_combo.setCurrentIndex(plugin.levels_bin_combo.findData(256))
    assert plugin.settings.global_.levels.bin_res == 256

    plugin.levels_factor_spin.setValue(50.0)
    assert plugin.settings.global_.levels.factor == pytest.approx(50.0)


# Luma Tab Controls & Lifecycle
def test_luma_tab_controls_and_lifecycle(populated_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange
    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act: Switch to Luma tab
    plugin.tab_widget.setCurrentIndex(1)

    # Assert toolbar controls update settings
    plugin.luma_shift_combo.setCurrentIndex(plugin.luma_shift_combo.findData(2))
    assert plugin.settings.global_.luma.shift == 2

    plugin.luma_sawtooth_checkbox.setChecked(True)
    assert plugin.settings.global_.luma.sawtooth is True

    # Act: Trigger update_histogram - Luma tab is self-managed by GraphicsView, must return cleanly
    plugin.update_histogram(0).result()


# Vectorscope Tab Across Formats & Modes
@pytest.mark.parametrize("yuv_format", [vs.YUV420P8, vs.YUV420P10])
def test_vectorscope_tab_yuv(
    populated_controller: HarnessPluginController, qtbot: QtBot, yuv_format: vs.PresetVideoFormat
) -> None:
    # Arrange
    voutput = make_video_output(yuv_format)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act
    plugin.tab_widget.setCurrentIndex(2)
    plugin.update_histogram(0).result()

    # Matrix selection
    plugin.vectorscope_matrix_combo.setCurrentIndex(plugin.vectorscope_matrix_combo.findData("bt601"))
    assert plugin.settings.global_.vectorscope.matrix == "bt601"

    # Render mode and luma spinbox enable state
    # Chroma wheel -> luma spin enabled
    plugin.vectorscope_mode_combo.setCurrentIndex(plugin.vectorscope_mode_combo.findData(RenderMode.CHROMA_WHEEL))
    assert plugin.settings.global_.vectorscope.mode == RenderMode.CHROMA_WHEEL
    assert plugin.vectorscope_luma_spin.isEnabled() is True

    # Density -> luma spin disabled
    plugin.vectorscope_mode_combo.setCurrentIndex(plugin.vectorscope_mode_combo.findData(RenderMode.DENSITY))
    assert plugin.settings.global_.vectorscope.mode == RenderMode.DENSITY
    assert plugin.vectorscope_luma_spin.isEnabled() is False

    # Luma adjustment
    plugin.vectorscope_luma_spin.setValue(1.25)
    assert plugin.settings.global_.vectorscope.luma == pytest.approx(1.25)


@pytest.mark.parametrize("non_yuv_format", [vs.RGB24, vs.GRAY8])
def test_vectorscope_tab_non_yuv_graceful_handling(
    populated_controller: HarnessPluginController,
    qtbot: QtBot,
    non_yuv_format: vs.PresetVideoFormat,
) -> None:
    # Arrange
    voutput = make_video_output(non_yuv_format)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act: Non-YUV should not raise and should clear scope safely
    plugin.tab_widget.setCurrentIndex(2)
    plugin.update_histogram(0).result()


# Waveform Tab Across Formats & Controls
@pytest.mark.parametrize("preset_format", [vs.YUV420P8, vs.RGB24, vs.GRAY8])
def test_waveform_tab_across_formats_and_controls(
    populated_controller: HarnessPluginController,
    qtbot: QtBot,
    preset_format: vs.PresetVideoFormat,
) -> None:
    # Arrange
    voutput = make_video_output(preset_format)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act
    plugin.tab_widget.setCurrentIndex(3)
    plugin.update_histogram(0).result()

    # Controls
    plugin.waveform_zones_checkbox.setChecked(False)
    assert plugin.settings.global_.waveform.show_zones is False

    plugin.waveform_dynamic_checkbox.setChecked(False)
    assert plugin.settings.global_.waveform.dynamic_gain is False

    plugin.waveform_gain_spin.setValue(2.5)
    assert plugin.settings.global_.waveform.gain == pytest.approx(2.5)


# CIE Chromaticity Tab & Error State Handling
def test_cie_tab_valid_rgb(populated_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange: Populated controller contains valid RGB24 with BT.709 primaries and transfer
    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act
    plugin.tab_widget.setCurrentIndex(4)
    plugin.update_histogram(0).result()

    # Assert no error displayed
    assert plugin.cie_container.cie_diagram.error_reason is None

    # CIE Mode
    plugin.cie_mode_combo.setCurrentIndex(plugin.cie_mode_combo.findData(CieMode.CIE_1976))
    assert plugin.settings.global_.cie.mode == CieMode.CIE_1976

    # Render mode & luma spinbox state verification (audit LOW-02 fix)
    # Density mode -> luma spinbox disabled
    plugin.cie_render_mode_combo.setCurrentIndex(plugin.cie_render_mode_combo.findData(RenderMode.DENSITY))
    assert plugin.settings.global_.cie.render_mode == RenderMode.DENSITY
    assert plugin.cie_luma_spin.isEnabled() is False

    # Chroma Wheel mode -> luma spinbox enabled
    plugin.cie_render_mode_combo.setCurrentIndex(plugin.cie_render_mode_combo.findData(RenderMode.CHROMA_WHEEL))
    assert plugin.settings.global_.cie.render_mode == RenderMode.CHROMA_WHEEL
    assert plugin.cie_luma_spin.isEnabled() is True

    # Pixel Color mode -> luma spinbox enabled
    plugin.cie_render_mode_combo.setCurrentIndex(plugin.cie_render_mode_combo.findData(RenderMode.PIXEL_COLOR))
    assert plugin.settings.global_.cie.render_mode == RenderMode.PIXEL_COLOR
    assert plugin.cie_luma_spin.isEnabled() is True

    # Gamut checkboxes
    plugin.cie_rec709_checkbox.setChecked(False)
    assert plugin.settings.global_.cie.show_rec709 is False

    plugin.cie_rec601_checkbox.setChecked(True)
    assert plugin.settings.global_.cie.show_rec601 is True

    plugin.cie_rec2020_checkbox.setChecked(False)
    assert plugin.settings.global_.cie.show_rec2020 is False

    plugin.cie_dcip3_checkbox.setChecked(False)
    assert plugin.settings.global_.cie.show_dcip3 is False


def test_cie_tab_rejects_non_rgb(populated_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange: YUV clip provided
    voutput = make_video_output(vs.YUV420P8)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act
    plugin.tab_widget.setCurrentIndex(4)
    plugin.update_histogram(0).result()

    # Assert explicit domain error
    assert plugin.cie_container.cie_diagram.error_reason == "CIE Chromaticity Diagram requires RGB input."


@pytest.mark.parametrize(
    ("primaries", "transfer"),
    [
        (vs.PRIMARIES_UNSPECIFIED, vs.TRANSFER_BT709),
        (vs.PRIMARIES_BT709, vs.TRANSFER_UNSPECIFIED),
        (vs.PRIMARIES_UNSPECIFIED, vs.TRANSFER_UNSPECIFIED),
    ],
)
def test_cie_tab_rejects_unspecified_primaries_or_transfer(
    populated_controller: HarnessPluginController, qtbot: QtBot, primaries: int, transfer: int
) -> None:
    # Arrange: RGB clip with invalid / unspecified color properties
    voutput = make_video_output(vs.RGB24, matrix=vs.MATRIX_RGB, primaries=primaries, transfer=transfer)
    populated_controller.set_current_voutput(voutput)

    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act
    plugin.tab_widget.setCurrentIndex(4)
    plugin.update_histogram(0).result()

    # Assert explicit domain error
    assert (
        plugin.cie_container.cie_diagram.error_reason
        == "CIE Chromaticity Diagram requires valid primaries and transfer frame properties."
    )


# Fault Tolerance: No Active Video Output
def test_histogram_plugin_no_video_output_safety(plugin_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange: Controller with no video output loaded
    plugin = plugin_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act & Assert: Operations must not raise uncaught NoCurrentVideoOutputError
    for tab_idx in range(5):
        plugin.tab_widget.setCurrentIndex(tab_idx)


# Frame Navigation Forwarding
def test_histogram_frame_navigation(populated_controller: HarnessPluginController, qtbot: QtBot) -> None:
    # Arrange
    plugin = populated_controller.create_plugin(HistogramPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    # Act & Assert: Seek to frame 5
    populated_controller.set_current_frame(5)
    assert populated_controller.current_frame == 5


# CustomQImage Unit Tests
def test_custom_qimage_properties_and_write_to() -> None:
    # Null image has ptr 0
    null_img = CustomQImage()
    assert null_img.isNull() is True
    assert null_img.ptr == 0

    # Valid image has non-zero pointer
    img = CustomQImage(32, 32, QImage.Format.Format_RGBX8888)
    assert img.isNull() is False
    assert img.ptr > 0

    # 2D grayscale data write
    data_2d = np.full((32, 32), 128, dtype=np.uint8)
    img_indexed = CustomQImage(32, 32, QImage.Format.Format_Indexed8)
    written_indexed = img_indexed.write_to(data_2d, QImage.Format.Format_Indexed8)
    assert written_indexed.width() == 32
    assert written_indexed.height() == 32
    assert written_indexed.format() == QImage.Format.Format_Indexed8

    # 4-channel RGBA data write
    data_4d = np.full((32, 32, 4), 255, dtype=np.uint8)
    written_rgba = img.write_to(data_4d, QImage.Format.Format_RGBX8888)
    assert written_rgba.width() == 32
    assert written_rgba.height() == 32
    assert written_rgba.format() == QImage.Format.Format_RGBX8888

    # Dimension resize on write_to
    larger_data = np.zeros((64, 64, 4), dtype=np.uint8)
    resized_img = img.write_to(larger_data, QImage.Format.Format_RGBX8888)
    assert resized_img.width() == 64
    assert resized_img.height() == 64


# Rust PyO3 funcs Extension Sanity
@pytest.mark.vpy("unique-core")
def test_funcs_native_extension_bindings() -> None:
    clip = vs.core.std.BlankClip(format=vs.YUV420P8, width=64, height=64, length=1)
    with clip.get_frame(0) as frame:
        # Waveform compute
        dst_wf = CustomQImage(64, 64, QImage.Format.Format_Indexed8)
        dst_wf.fill(0)
        funcs.compute_waveform(
            src=(frame.get_read_ptr(0).value or 0, frame.get_stride(0)),
            dst=(dst_wf.ptr, dst_wf.bytesPerLine()),
            width=frame.width,
            height=frame.height,
            bits=frame.format.bits_per_sample,
            sample_type=frame.format.sample_type.value,
            target_w=64,
            target_h=64,
            is_chroma=False,
            is_limited=True,
            gain=1.0,
            dynamic_gain=True,
        )
        assert dst_wf.isNull() is False

        # Vectorscope compute
        dst_vs = CustomQImage(64, 64, QImage.Format.Format_Indexed8)
        dst_vs.fill(0)
        matrix_coeffs = [
            1.0,
            1.0,
            1.0,
            0.0,
            -0.187324,
            1.8556,
            1.5748,
            -0.468124,
            0.0,
        ]
        funcs.compute_vectorscope(
            src_yuv=[
                (frame.get_read_ptr(0).value or 0, frame.get_stride(0)),
                (frame.get_read_ptr(1).value or 0, frame.get_stride(1)),
                (frame.get_read_ptr(2).value or 0, frame.get_stride(2)),
            ],
            dst=(dst_vs.ptr, dst_vs.bytesPerLine()),
            width=frame.width // 2,
            height=frame.height // 2,
            bits=frame.format.bits_per_sample,
            sample_type=frame.format.sample_type.value,
            subsampling_w=1,
            subsampling_h=1,
            is_limited=True,
            matrix_coeffs=matrix_coeffs,
            canvas_size=64,
            render_mode=RenderMode.DENSITY,
            luma_scale=1.0,
        )
        assert dst_vs.isNull() is False

        # Luma compute
        dst_luma = CustomQImage(64, 64, QImage.Format.Format_Indexed8)
        dst_luma.fill(0)
        funcs.compute_luma(
            src=(frame.get_read_ptr(0).value or 0, frame.get_stride(0)),
            dst=(dst_luma.ptr, dst_luma.bytesPerLine()),
            width=frame.width,
            height=frame.height,
            bits=frame.format.bits_per_sample,
            sample_type=frame.format.sample_type.value,
            shift_in=4,
            use_sawtooth=False,
            is_limited=True,
        )
        assert dst_luma.isNull() is False

    # CIE compute
    clip_xyz = vs.core.std.BlankClip(format=vs.RGBS, width=64, height=64, length=1)
    with clip_xyz.get_frame(0) as frame_xyz:
        dst_cie = CustomQImage(64, 64, QImage.Format.Format_Indexed8)
        dst_cie.fill(0)
        funcs.compute_cie(
            src_xyz=[
                (frame_xyz.get_read_ptr(0).value or 0, frame_xyz.get_stride(0)),
                (frame_xyz.get_read_ptr(1).value or 0, frame_xyz.get_stride(1)),
                (frame_xyz.get_read_ptr(2).value or 0, frame_xyz.get_stride(2)),
            ],
            dst=(dst_cie.ptr, dst_cie.bytesPerLine()),
            width=frame_xyz.width,
            height=frame_xyz.height,
            canvas_size=64,
            cie_mode=CieMode.CIE_1931,
            render_mode=RenderMode.DENSITY,
            luma_scale=1.0,
            linear_rgb=None,
        )
        assert dst_cie.isNull() is False
