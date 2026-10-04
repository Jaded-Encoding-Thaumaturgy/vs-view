from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from functools import cache
from logging import getLogger
from typing import assert_never, override

import numpy as np
import vapoursynth as vs
from jetpytools import cachedproperty
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget
from vstools import Matrix, Range

from vsview.api import PluginAPI, PluginSettings

from ..funcs import compute_vectorscope
from ..settings import GlobalSettings, RenderMode
from ..utils import CustomContextMenu, CustomQImage

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
    def yuv_to_rgb_mat(self) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
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

_YUV_TO_RGB_COEFFS: dict[VectorScopeMatrix, tuple[float, ...]] = {
    matrix: tuple(arr.flatten().tolist()) for matrix, arr in _YUV_TO_RGB_MATS.items()
}


class VectorscopeWidget(QWidget):
    def __init__(self, parent: QWidget | None, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings

        self.scope_image = CustomQImage(128, 128, CustomQImage.Format.Format_Indexed8)
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

        if self.settings.global_.vectorscope.mode == RenderMode.CHROMA_WHEEL:
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

        if fmt.color_family != vs.YUV:
            self.scope_image.fill(0)
            self.update()
            if fmt.color_family != vs.GRAY:
                logger.warning("%s input — no chroma data", fmt.color_family.name)
            return

        self.current_matrix = Matrix.from_video(frame, func=self.update_frame)

        match render_mode := self.settings.global_.vectorscope.mode:
            case RenderMode.CHROMA_WHEEL:
                target_format = CustomQImage.Format.Format_RGBX8888
            case RenderMode.PIXEL_COLOR:
                target_format = CustomQImage.Format.Format_RGBX8888
            case RenderMode.DENSITY:
                target_format = CustomQImage.Format.Format_Indexed8
            case _:
                assert_never(render_mode)

        if (res := self.settings.global_.vectorscope.res) == 0:
            size = min(1024, 1 << fmt.bits_per_sample)
        elif fmt.sample_type == vs.INTEGER:
            size = min(res, 1 << fmt.bits_per_sample)
        else:
            size = res

        if (
            self.scope_image.width() != size
            or self.scope_image.height() != size
            or self.scope_image.format() != target_format
        ):
            self.scope_image = CustomQImage(size, size, target_format)
            if target_format == CustomQImage.Format.Format_Indexed8:
                self.scope_image.setColorTable(self.color_table)

        compute_vectorscope(
            src_yuv=[(frame.get_read_ptr(i).value or 0, frame.get_stride(i)) for i in range(frame.format.num_planes)],
            dst=(self.scope_image.ptr, self.scope_image.bytesPerLine()),
            width=frame.width >> fmt.subsampling_w,
            height=frame.height >> fmt.subsampling_h,
            bits=fmt.bits_per_sample,
            sample_type=fmt.sample_type.value,
            subsampling_w=fmt.subsampling_w,
            subsampling_h=fmt.subsampling_h,
            is_limited=Range.from_video(frame).is_limited,
            matrix_coeffs=_YUV_TO_RGB_COEFFS[self._resolved_matrix],
            canvas_size=size,
            render_mode=render_mode,
            luma_scale=self.settings.global_.vectorscope.luma,
        )
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
def background_image(matrix: VectorScopeMatrix, size: int = 1024) -> CustomQImage:
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

    qimg = CustomQImage(size, size, CustomQImage.Format.Format_RGBX8888)

    return qimg.write_to(rgba, qimg.format())
