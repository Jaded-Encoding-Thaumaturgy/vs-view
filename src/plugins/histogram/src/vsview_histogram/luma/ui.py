from __future__ import annotations

from logging import getLogger
from typing import override

import numpy as np
import vapoursynth as vs
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from vstools import Range, get_lowest_value, get_peak_value

from vsview.api import PluginSettings

from ..settings import GlobalSettings

logger = getLogger(__name__)


class LumaWidget(QWidget):
    BACKGROUND_COLOR = QColor(20, 20, 20)

    def __init__(self, parent: QWidget, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.settings = settings
        self.scope_image = QImage()
        self.setMinimumHeight(128)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.fillRect(self.rect(), self.BACKGROUND_COLOR)

        if self.scope_image.isNull():
            return

        img_w = self.scope_image.width()
        img_h = self.scope_image.height()
        widget_w = self.width()
        widget_h = self.height()

        scale = min(widget_w / img_w, widget_h / img_h)
        target_w = int(img_w * scale)
        target_h = int(img_h * scale)

        x = (widget_w - target_w) // 2
        y = (widget_h - target_h) // 2

        painter.drawImage(QRect(x, y, target_w, target_h), self.scope_image)

    def update_data(self, frame: vs.VideoFrame) -> None:
        # Extract luma plane
        if frame.format.color_family == vs.RGB:
            logger.warning("RGB input — no luma data")
            self.scope_image.fill(0)
            self.update()
            return

        arr = np.asarray(frame[0])
        h, w = arr.shape

        # Determine step for downsampling
        if (res := self.settings.global_.luma.res) == 0:
            target_w = max(256, self.width())
            target_h = max(144, self.height())
            step = max(1, w // target_w, h // target_h)
        else:
            step = max(1, w // res)

        # Zero-copy downsampling slice
        arr_down = arr[::step, ::step]

        from .numba_backend import process_luma_numba

        bits = frame.format.bits_per_sample
        shift_in = self.settings.global_.luma.shift
        use_sawtooth = self.settings.global_.luma.sawtooth

        if frame.format.sample_type == vs.FLOAT:
            color_range = Range.from_video(frame)
            output_peak = get_peak_value(16, False, color_range, frame.format.color_family)
            output_lowest = get_lowest_value(16, False, color_range, frame.format.color_family)

            scale = (output_peak - output_lowest) / 65535.0
            offset = (16 << 8) / 65535.0 if color_range.is_limited else 0.0

            arr_down = (arr_down * scale + offset).astype(np.float32)
            max_val = 65535
            shift_out = 8
        else:
            max_val = (1 << bits) - 1
            shift_out = max(0, bits - 8)

        dst_arr = np.empty(arr_down.shape, dtype=np.uint8)

        process_luma_numba(arr_down, dst_arr, max_val, shift_out, shift_in, use_sawtooth)

        self.scope_image = QImage(
            dst_arr,  # type: ignore[call-overload]
            *dst_arr.shape[::-1],
            dst_arr.strides[0],
            QImage.Format.Format_Grayscale8,
        ).copy()
        self.update()

    def clear(self) -> None:
        self.scope_image = QImage()
        self.update()


class LumaContainerWidget(QFrame):
    def __init__(self, parent: QWidget, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)

        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)

        self.luma_widget = LumaWidget(self, self.settings)
        self.current_layout.addWidget(self.luma_widget)

    def update_luma(self, frame: vs.VideoFrame) -> None:
        self.luma_widget.update_data(frame)
