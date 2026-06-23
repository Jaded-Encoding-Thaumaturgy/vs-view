from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from functools import cache
from logging import getLogger
from typing import Literal, override

import numpy as np
import vapoursynth as vs
from jetpytools import cachedproperty
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget
from vstools import Matrix, Range, get_lowest_value, get_peak_value

from vsview.api import PluginAPI, PluginSettings

from ..settings import GlobalSettings
from ..utils import CustomContextMenu

logger = getLogger(__name__)

# Math to calculate a target coordinate on the 256x256 canvas from RGB (range 0-1) and matrix coefficients (Kr, Kb):
#   Kg = 1.0 - Kr - Kb
#   Y = Kr * R + Kg * G + Kb * B
#   U_val = (B - Y) / (2 * (1 - Kb))
#   V_val = (R - Y) / (2 * (1 - Kr))
#   x = 128.0 + U_val * (240 - 16)
#   y = 128.0 - V_val * (240 - 16)
BT709_TARGETS = {
    "R": (102, 16),
    "M": (214, 26),
    "B": (240, 138),
    "C": (154, 240),
    "G": (42, 230),
    "Y": (16, 118),
}
BT2020_TARGETS = {
    "R": (97, 16),
    "M": (209, 25),
    "B": (240, 137),
    "C": (159, 240),
    "G": (47, 231),
    "Y": (16, 119),
}
BT601_TARGETS = {
    "R": (90, 16),
    "M": (202, 34),
    "B": (240, 146),
    "C": (166, 240),
    "G": (54, 222),
    "Y": (16, 110),
}
ST240M_TARGETS = {
    "R": (102, 16),
    "M": (214, 28),
    "B": (240, 140),
    "C": (154, 240),
    "G": (42, 228),
    "Y": (16, 116),
}


class VectorScopeMatrix(StrEnum):
    BT709 = "bt709"
    BT601 = "bt601"
    BT2020_NCL = "bt2020"
    ST240_M = "st240m"

    @property
    def yuv_to_rgb_mat(self) -> np.ndarray[tuple[Literal[3], Literal[3]], np.dtype[np.float32]]:
        return _YUV_TO_RGB_MATS[self]

    @property
    def targets(self) -> dict[str, tuple[int, int]]:
        return _TARGETS[self]

    @classmethod
    def from_matrix(cls, current: Matrix) -> VectorScopeMatrix:
        match current:
            case Matrix.BT470_BG | Matrix.ST170_M:
                return VectorScopeMatrix.BT601
            case Matrix.BT2020_NCL | Matrix.BT2020_CL:
                return VectorScopeMatrix.BT2020_NCL
            case Matrix.ST240_M:
                return VectorScopeMatrix.ST240_M
            case _:
                return VectorScopeMatrix.BT709


_YUV_TO_RGB_MATS = {
    VectorScopeMatrix.BT601: np.asarray(
        [
            [1.000000, 1.000000, 1.000000],
            [0.000000, -0.344136, 1.772000],
            [1.402000, -0.714136, 0.000000],
        ],
        dtype=np.float32,
    ),
    VectorScopeMatrix.BT2020_NCL: np.asarray(
        [
            [1.000000, 1.000000, 1.000000],
            [0.000000, -0.164553, 1.881400],
            [1.474600, -0.571353, 0.000000],
        ],
        dtype=np.float32,
    ),
    VectorScopeMatrix.ST240_M: np.asarray(
        [
            [1.000000, 1.000000, 1.000000],
            [0.000000, -0.226622, 1.826000],
            [1.576000, -0.476622, 0.000000],
        ],
        dtype=np.float32,
    ),
    VectorScopeMatrix.BT709: np.asarray(
        [
            [1.000000, 1.000000, 1.000000],
            [0.000000, -0.187324, 1.855600],
            [1.574800, -0.468124, 0.000000],
        ],
        dtype=np.float32,
    ),
}

_TARGETS = {
    VectorScopeMatrix.BT709: BT709_TARGETS,
    VectorScopeMatrix.BT601: BT601_TARGETS,
    VectorScopeMatrix.BT2020_NCL: BT2020_TARGETS,
    VectorScopeMatrix.ST240_M: ST240M_TARGETS,
}


class VectorscopeWidget(QWidget):
    def __init__(self, parent: QWidget | None, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings
        self.setMinimumSize(128, 128)

        self.scope_image = QImage(128, 128, QImage.Format.Format_Indexed8)
        self.scope_image.setColorTable(self.color_table)
        self.scope_image.fill(0)

        self.context_menu = CustomContextMenu(self, self.api)
        self.current_matrix = Matrix.UNSPECIFIED

    @cachedproperty
    def color_table(self) -> Sequence[int]:
        """Neon cyan/blue phosphor color table"""
        colors = list[int]()
        for i in range(256):
            r = max(0, i - 192) * 4
            g = min(255, int(i * 1.1))
            b = min(255, int(i * 1.3))
            colors.append(QColor(r, g, b, 255).rgba())
        return colors

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        painter.fillRect(self.rect(), QColor(20, 20, 20))

        # Draw square vectorscope centering inside widget bounds
        side = min(self.width(), self.height())
        target_rect = QRect((self.width() - side) // 2, (self.height() - side) // 2, side, side)

        if self.settings.global_.vectorscope.mode == "chroma_wheel":
            # Draw high-resolution background color wheel first
            painter.drawImage(target_rect, background_image(self._resolved_matrix))

            # Use additive blending to draw the density points on top
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.drawImage(target_rect, self.scope_image)
            painter.restore()
        else:
            painter.drawImage(target_rect, self.scope_image)

        # Draw graticules overlay
        self.draw_graticules(painter, target_rect)

    def draw_graticules(self, painter: QPainter, rect: QRect) -> None:
        painter.save()
        painter.translate(rect.left(), rect.top())
        painter.scale(rect.width() / 256.0, rect.height() / 256.0)

        # Center axes
        painter.setPen(QPen(QColor(80, 80, 80, 150), 1, Qt.PenStyle.DashLine))
        painter.drawLine(128, 0, 128, 256)
        painter.drawLine(0, 128, 256, 128)

        # 75% and 100% saturation circles (radii 87 and 114)
        painter.setPen(QPen(QColor(100, 100, 100, 150), 1, Qt.PenStyle.SolidLine))
        painter.drawEllipse(QPointF(128.0, 128.0), 87.0, 87.0)
        painter.drawEllipse(QPointF(128.0, 128.0), 114.0, 114.0)

        # Skin tone reference line (123 degrees: from center to top-left)
        painter.setPen(QPen(QColor(244, 164, 96, 180), 1, Qt.PenStyle.SolidLine))
        painter.drawLine(128, 128, 66, 32)

        painter.setPen(QPen(QColor(180, 180, 180, 200), 1))
        font = painter.font()
        font.setPointSize(6)
        painter.setFont(font)

        for label, (u, v) in self._resolved_matrix.targets.items():
            painter.drawRect(u - 3, v - 3, 6, 6)
            painter.drawText(u + 5, v + 3, label)

        painter.restore()

    def update_frame(self, frame: vs.VideoFrame) -> None:
        fmt = frame.format

        if (res := self.settings.global_.vectorscope.res) == 0:
            size = min(1024, 1 << fmt.bits_per_sample)
        elif fmt.sample_type == vs.INTEGER:
            size = min(res, 1 << fmt.bits_per_sample)
        else:
            size = res
        bits = size.bit_length() - 1
        neutral = size // 2

        match fmt.color_family:
            case vs.GRAY:
                # Grayscale has neutral chroma
                yuv_scaled = np.full((3, 16, 16), neutral, dtype=np.int32)
            case vs.YUV:
                y = np.asarray(frame[0])
                u = np.asarray(frame[1])
                v = np.asarray(frame[2])

                # Get subsampling factors (row stride = height, col stride = width)
                y_aligned = y[:: 2**fmt.subsampling_h, :: 2**fmt.subsampling_w][: u.shape[0], : u.shape[1]]
                yuv = np.stack([y_aligned, u, v], axis=0)

                if fmt.sample_type == vs.FLOAT:
                    color_range = Range.from_video(frame)
                    yuv_scaled = np.empty_like(yuv, dtype=np.int32)

                    # Scale Y (plane 0)
                    peak_y = get_peak_value(bits, range_in=color_range, family=fmt.color_family)
                    lowest_y = get_lowest_value(bits, range_in=color_range, family=fmt.color_family)
                    y_sc = yuv[0] * (peak_y - lowest_y)
                    if color_range.is_limited:
                        y_sc += size // 16
                    yuv_scaled[0] = y_sc

                    # Scale U and V (planes 1 and 2)
                    peak_c = get_peak_value(bits, chroma=True, range_in=color_range, family=fmt.color_family)
                    lowest_c = get_lowest_value(bits, chroma=True, range_in=color_range, family=fmt.color_family)
                    yuv_scaled[1:] = yuv[1:] * (peak_c - lowest_c) + neutral
                else:
                    shift = fmt.bits_per_sample - bits
                    yuv_int = yuv.astype(np.int32)
                    if shift > 0:
                        yuv_scaled = yuv_int >> shift
                    elif shift < 0:
                        yuv_scaled = yuv_int << (-shift)
                    else:
                        yuv_scaled = yuv_int
            case _ as cfam:
                self.scope_image.fill(0)
                self.update()
                logger.warning("%s input — no chroma data", cfam.name)
                return

        self.current_matrix = Matrix.from_video(frame, func=self.update_frame)
        yuv_scaled = yuv_scaled.clip(0, size - 1)

        if yuv_scaled.dtype != np.int32:
            yuv_scaled = yuv_scaled.round().astype(np.int32)

        if self.settings.global_.vectorscope.mode == "pixel_color":
            total_elements = yuv_scaled.shape[1] * yuv_scaled.shape[2]

            # Apply safety stride for very large resolutions (e.g. 4K 4:4:4)
            stride = max(1, int(np.sqrt(total_elements / 2_000_000))) if total_elements > 2_000_000 else 1
            yuv_sliced = yuv_scaled[:, ::stride, ::stride].reshape(3, -1)

            # Map coordinates (size - V, U)
            y_coords = np.clip(size - yuv_sliced[2], 0, size - 1)
            x_coords = yuv_sliced[1]

            # Convert to RGB
            yuv_flat = yuv_sliced.astype(np.float32)
            scale_factor = size / 256.0
            yuv_flat[0] /= scale_factor
            yuv_flat[1:] = (yuv_flat[1:] - neutral) / scale_factor
            rgb = (yuv_flat.T @ self._resolved_matrix.yuv_to_rgb_mat).clip(0, 255).astype(np.uint8)

            # Draw into canvas
            grid = np.zeros((size, size, 4), dtype=np.uint8)
            grid[y_coords, x_coords, :3] = rgb

            self.scope_image = QImage(grid, size, size, size * 4, QImage.Format.Format_RGBX8888).copy()  # type: ignore[call-overload]
            self.update()
            return

        # Density and Chroma Wheel construct a density histogram grid
        # Map to 2D grid index (Y is size - V, X is U)
        y_coords = np.clip(size - yuv_scaled[2].ravel(), 0, size - 1)
        indices = y_coords * size + yuv_scaled[1].ravel()
        bins = np.bincount(indices, minlength=(size * size))
        grid = bins.reshape((size, size))

        # Logarithmic density scale
        if (max_val := grid.max()) > 0:
            scale = 255.0 / np.log1p(max_val)
            grid_img = (np.log1p(grid) * scale).astype(np.uint8)
        else:
            grid_img = grid.astype(np.uint8)

        if self.settings.global_.vectorscope.mode == "chroma_wheel":
            # Color Mode: Density map over a high-resolution YUV color wheel background
            y_idx, x_idx = np.nonzero(grid_img)
            density = grid_img[y_idx, x_idx].astype(np.float32) / 255.0

            scale_factor = size / 256.0
            u_val = (x_idx.astype(np.float32) - neutral) / scale_factor
            v_val = (np.clip(size - y_idx, 0, size - 1).astype(np.float32) - neutral) / scale_factor

            # Use fixed moderate luma for accurate hue, then scale by density for brightness
            luma = np.full_like(density, self.settings.global_.vectorscope.luma)
            base_rgb = (np.column_stack([luma, u_val, v_val]) @ self._resolved_matrix.yuv_to_rgb_mat).clip(0, 255)
            colored = (base_rgb * density[:, np.newaxis]).clip(0, 255).astype(np.uint8)

            rgb = np.zeros((size, size, 4), dtype=np.uint8)
            rgb[y_idx, x_idx, :3] = colored

            self.scope_image = QImage(rgb, size, size, size * 4, QImage.Format.Format_RGBX8888).copy()  # type: ignore[call-overload]
        else:
            # Density mode
            self.scope_image = QImage(grid_img, size, size, size, QImage.Format.Format_Indexed8).copy()  # type: ignore[arg-type]
            self.scope_image.setColorTable(self.color_table)

        self.update()

    @property
    def _resolved_matrix(self) -> VectorScopeMatrix:
        return (
            VectorScopeMatrix.from_matrix(self.current_matrix)
            if (matrix := self.settings.global_.vectorscope.matrix) == "auto"
            else VectorScopeMatrix(matrix)
        )


class VectorscopeContainerWidget(QFrame):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)

        self.current_layout = QVBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)

        self.vectorscope = VectorscopeWidget(self, self.api, self.settings)
        self.current_layout.addWidget(self.vectorscope)

    def update_histogram(self, frame: vs.VideoFrame) -> None:
        self.vectorscope.update_frame(frame)


@cache
def background_image(matrix: VectorScopeMatrix, size: int = 1024) -> QImage:
    pixel_scale = 256.0 / size
    u_bg = np.tile(np.arange(size, dtype=np.float32) * pixel_scale, (size, 1))
    v_bg = (256.0 - np.tile(np.arange(size, dtype=np.float32).reshape(size, 1) * pixel_scale, (1, size))).clip(0, 255)
    dist = np.sqrt((u_bg - 128.0) ** 2 + (v_bg - 128.0) ** 2)

    # Soft transition for anti-aliasing the circle edge (radius 114)
    weight = np.clip((114.125 - dist) * 4.0, 0.0, 1.0)

    u_bg_masked = 128.0 + weight * (u_bg - 128.0)
    v_bg_masked = 128.0 + weight * (v_bg - 128.0)
    y_bg = 16.0 + weight * (64.0 - 16.0)

    yuv_bg = np.dstack([y_bg, u_bg_masked - 128.0, v_bg_masked - 128.0])
    rgb = (yuv_bg @ matrix.yuv_to_rgb_mat).clip(0, 255).astype(np.uint8)

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., :3] = rgb

    return QImage(rgba, size, size, size * 4, QImage.Format.Format_RGBX8888).copy()  # type: ignore[call-overload]
