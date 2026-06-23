from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, override

import numpy as np
import numpy.typing as npt
import vapoursynth as vs
from jetpytools import cachedproperty, classproperty
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from vstools import Range, get_lowest_value, get_peak_value

from vsview.api import PluginAPI, PluginSettings

from ..settings import GlobalSettings
from ..utils import CustomContextMenu


class ColorName(StrEnum):
    R = "red"
    G = "green"
    B = "blue"
    Y = "y"
    U = "u"
    V = "v"
    LUMA = "luma"

    @classproperty
    @classmethod
    def rgb(cls) -> Sequence[ColorName]:
        return [ColorName.R, ColorName.G, ColorName.B]

    @classproperty
    @classmethod
    def yuv(cls) -> Sequence[ColorName]:
        return [ColorName.Y, ColorName.U, ColorName.V]

    @cachedproperty
    def table(self) -> npt.NDArray[np.uint32]:
        table = list[int]()
        for i in range(256):
            match self:
                case ColorName.R:
                    c = QColor(i, 0, 0, 255)
                case ColorName.G:
                    c = QColor(0, i, 0, 255)
                case ColorName.B:
                    c = QColor(0, 0, i, 255)
                case ColorName.Y | ColorName.LUMA:
                    c = QColor(int(i * 0.8), i, i, 255)
                case ColorName.U:
                    c = QColor(0, int(i * 0.8), i, 255)
                case ColorName.V:
                    c = QColor(i, 0, int(i * 0.8), 255)
            table.append(c.rgba())

        return np.asarray(table, dtype=np.uint32)


class WaveformWidget(QWidget):
    BACKGROUND_COLOR = QColor(20, 20, 20)
    NEUTRAL_PEN = QPen(QColor(80, 80, 80), 1, Qt.PenStyle.DashLine)
    LABEL_PEN = QPen(QColor(180, 180, 180), 1)
    UNSAFE_FILL_COLOR = QColor(244, 67, 54, 25)
    UNSAFE_BORDER_PEN = QPen(QColor(244, 67, 54, 120), 1, Qt.PenStyle.DashLine)

    def __init__(
        self,
        parent: QWidget,
        api: PluginAPI,
        settings: PluginSettings[GlobalSettings, None],
        color_name: ColorName,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings
        self.color_name = color_name
        self.scope_image = QImage()
        self._is_chroma = False
        self._color_family: vs.ColorFamily = vs.UNDEFINED
        self.setMinimumHeight(150)

        self.context_menu = CustomContextMenu(self, self.api)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        painter.fillRect(self.rect(), self.BACKGROUND_COLOR)

        if self.scope_image.isNull():
            return

        # Scale QImage to fill widget
        painter.drawImage(self.rect(), self.scope_image)

        # Draw zones and graticules
        if self.settings.global_.waveform.show_zones:
            h = self.height()
            w = self.width()

            # Neutral line (128)
            painter.setPen(self.NEUTRAL_PEN)
            painter.drawLine(0, h // 2, w, h // 2)

            # Font setup for labels
            painter.setPen(self.LABEL_PEN)
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(8, h // 2 - 4, "Neutral")

            # Draw YUV/GRAY limit lines and shading
            if self._color_family in (vs.YUV, vs.GRAY):
                if self._color_family == vs.YUV and self._is_chroma:
                    y_black_val = 16
                    y_white_val = 240
                    black_label = "Chroma Min"
                    white_label = "Chroma Max"
                else:
                    y_black_val = 16
                    y_white_val = 235
                    black_label = "Black Limit"
                    white_label = "White Limit"

                y_black = int(h * (255 - y_black_val) / 255)
                y_white = int(h * (255 - y_white_val) / 255)

                painter.fillRect(0, 0, w, y_white, self.UNSAFE_FILL_COLOR)
                painter.fillRect(0, y_black, w, h - y_black, self.UNSAFE_FILL_COLOR)

                # Draw boundary lines
                painter.setPen(self.UNSAFE_BORDER_PEN)
                painter.drawLine(0, y_black, w, y_black)
                painter.drawLine(0, y_white, w, y_white)

                # Labels for limit lines
                painter.setPen(self.LABEL_PEN)
                painter.drawText(8, y_white - 4, white_label)
                painter.drawText(8, y_black - 4, black_label)

    def update_data(self, arr: npt.NDArray[Any], frame: vs.VideoFrame, chroma: bool = False) -> None:
        fmt = frame.format
        self._is_chroma = chroma
        self._color_family = fmt.color_family

        if (res := self.settings.global_.waveform.res) == 0:
            target_h = min(1024, 1 << fmt.bits_per_sample)
        elif fmt.sample_type == vs.INTEGER:
            target_h = min(res, 1 << fmt.bits_per_sample)
        else:
            target_h = res
        bits = target_h.bit_length() - 1

        # Scale input plane array to target range [0, target_h - 1]
        if fmt.sample_type == vs.FLOAT:
            color_range = Range.from_video(frame)
            output_peak = get_peak_value(bits, chroma, color_range, fmt.color_family)
            output_lowest = get_lowest_value(bits, chroma, color_range, fmt.color_family)

            arr_scaled = arr * (output_peak - output_lowest)
            if chroma:
                arr_scaled += 128 << (bits - 8)
            elif color_range.is_limited:
                arr_scaled += 16 << (bits - 8)

            arr_scaled = arr_scaled.round().clip(0, target_h - 1).astype(np.int32)
        else:
            arr = arr.astype(np.int32)
            if (shift := fmt.bits_per_sample - bits) > 0:
                arr = (arr + (1 << (shift - 1))) >> shift
            elif shift < 0:
                arr <<= -shift

            arr_scaled = arr.clip(0, target_h - 1)

        # Downsample horizontally to a target width based on widget width for performance
        target_w = max(1024, self.width())
        h, w = arr_scaled.shape
        step = max(1, w // target_w)
        arr_down = arr_scaled[:, ::step]
        actual_w = arr_down.shape[1]

        # Vectorized column-wise bincount
        cols = np.tile(np.arange(actual_w, dtype=np.int32), (h, 1))
        grid = np.bincount((arr_down * actual_w + cols).ravel(), minlength=target_h * actual_w).reshape(
            (target_h, actual_w)
        )

        # Flip vertically so value target_h-1 is top (row 0), 0 is bottom (row target_h-1)
        grid = np.flipud(grid)

        # Logarithmic density scale (0 to 255 index range)
        if self.settings.global_.waveform.dynamic_gain:
            scale = 255.0 / np.log1p(max_val) if (max_val := grid.max()) > 0 else 0.0
        else:
            # Static gain reference is the height of the frame
            scale = 255.0 / np.log1p(h)

        if scale > 0.0:
            grid = np.log1p(grid) * scale * self.settings.global_.waveform.gain

        # Map the index values (0-255) to the RGB32 color table to enable smooth scaling in Qt
        grid_rgba = self.color_name.table[grid.clip(0, 255).astype(np.uint8)]

        # Update QImage as RGB32
        self.scope_image = QImage(grid_rgba, actual_w, target_h, actual_w * 4, QImage.Format.Format_RGB32).copy()  # type: ignore[arg-type]
        self.update()

    def clear(self) -> None:
        self.scope_image = QImage()
        self.update()


class WaveformContainerWidget(QFrame):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)

        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(4)

        self.waveforms = list[WaveformWidget]()

    def update_histogram(self, frame: vs.VideoFrame) -> None:
        self.setup_layout(frame.format.color_family)

        match self.settings.global_.waveform.mode:
            case "luma" if frame.format.color_family == vs.RGB:
                self.waveforms[0].clear()
            case "luma":
                self.waveforms[0].update_data(np.asarray(frame[0]), frame)
            case "parade":
                for i in range(frame.format.num_planes):
                    self.waveforms[i].update_data(
                        np.asarray(frame[i]),
                        frame,
                        chroma=frame.format.color_family is vs.YUV and i > 0,
                    )

    def setup_layout(self, color_family: vs.ColorFamily) -> None:
        if self.settings.global_.waveform.mode == "luma":
            needed_colors = [ColorName.LUMA]
        else:  # parade
            if color_family == vs.RGB:
                needed_colors = ColorName.rgb
            elif color_family == vs.YUV:
                needed_colors = ColorName.yuv
            else:  # GRAY
                needed_colors = [ColorName.Y]

        num_needed = len(needed_colors)

        # Only create waveforms if we don't have enough
        while len(self.waveforms) < num_needed:
            w = WaveformWidget(self, self.api, self.settings, needed_colors[len(self.waveforms)])
            self.waveforms.append(w)
            self.current_layout.addWidget(w, stretch=1)

        # Configure colors and show the ones we need
        for i in range(num_needed):
            w = self.waveforms[i]
            if w.color_name != needed_colors[i]:
                w.color_name = needed_colors[i]
                w.scope_image = QImage()
            w.show()

        # Hide extra widgets
        for i in range(num_needed, len(self.waveforms)):
            self.waveforms[i].hide()
