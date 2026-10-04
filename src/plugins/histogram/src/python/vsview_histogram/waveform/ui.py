from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from logging import getLogger
from typing import override

import vapoursynth as vs
from jetpytools import cachedproperty, classproperty
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from vstools import Range, get_plane_sizes

from vsview.api import PluginAPI, PluginSettings

from ..funcs import compute_waveform
from ..settings import GlobalSettings
from ..utils import CustomContextMenu, CustomQImage

logger = getLogger(__name__)


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
    def table_list(self) -> list[int]:
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
        return table


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
        self.scope_image = CustomQImage()
        self._is_chroma = False
        self._color_family: vs.ColorFamily = vs.UNDEFINED

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
            painter.setPen(QPen(QColor(220, 80, 80), 1))
            font = painter.font()
            font.setPointSize(20)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Luma mode requires GRAY or YUV input.")
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

    def update_data(self, frame: vs.VideoFrame, plane: int, chroma: bool = False) -> None:
        fmt = frame.format
        self._is_chroma = chroma
        self._color_family = fmt.color_family

        if (res := self.settings.global_.waveform.res) == 0:
            target_h = min(1024, 1 << fmt.bits_per_sample)
        elif fmt.sample_type == vs.INTEGER:
            target_h = min(res, 1 << fmt.bits_per_sample)
        else:
            target_h = res

        plane_w, plane_h = get_plane_sizes(frame, plane)

        target_w = max(1024, self.width())
        step = max(1, plane_w // target_w)
        actual_w = plane_w // step

        if (
            self.scope_image.width() != actual_w
            or self.scope_image.height() != target_h
            or self.scope_image.format() != CustomQImage.Format.Format_Indexed8
        ):
            self.scope_image = CustomQImage(actual_w, target_h, CustomQImage.Format.Format_Indexed8)
            self.scope_image.setColorTable(self.color_name.table_list)

        compute_waveform(
            src=(frame.get_read_ptr(plane).value or 0, frame.get_stride(plane)),
            dst=(self.scope_image.ptr, self.scope_image.bytesPerLine()),
            width=plane_w,
            height=plane_h,
            bits=fmt.bits_per_sample,
            sample_type=fmt.sample_type.value,
            target_w=actual_w,
            target_h=target_h,
            is_chroma=chroma,
            is_limited=Range.from_video(frame).is_limited,
            gain=self.settings.global_.waveform.gain,
            dynamic_gain=self.settings.global_.waveform.dynamic_gain,
        )
        self.update()

    def clear(self) -> None:
        self.scope_image = CustomQImage()
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
                logger.warning("RGB input — no luma data")
            case "luma":
                self.waveforms[0].update_data(frame, 0)
            case "parade":
                for i in range(frame.format.num_planes):
                    self.waveforms[i].update_data(
                        frame,
                        i,
                        chroma=frame.format.color_family == vs.YUV and i > 0,
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
                w.scope_image = CustomQImage()
            w.show()

        # Hide extra widgets
        for i in range(num_needed, len(self.waveforms)):
            self.waveforms[i].hide()
