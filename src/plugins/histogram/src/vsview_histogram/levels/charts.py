from __future__ import annotations

from collections.abc import Sequence
from typing import override

import numpy as np
import numpy.typing as npt
import vapoursynth as vs
from jetpytools import clamp
from PySide6.QtCharts import QAreaSeries, QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QGradient,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from vsview.api import PluginAPI

from ..utils import CustomContextMenu


class LevelsChartView(QChartView):
    UNSAFE_LEFT_COLOR = QColor(0, 0, 0, 50)
    UNSAFE_LEFT_Y_COLOR = QColor(255, 255, 255, 50)
    UNSAFE_RIGHT_COLOR = QColor(0, 0, 0, 50)

    UNSAFE_PEN = QPen(Qt.GlobalColor.transparent)
    AREA_SERIES_PEN = QPen(Qt.GlobalColor.transparent)

    GRID_PEN_COLOR = QColor(0, 0, 0, 200)
    GRID_PEN = QPen(GRID_PEN_COLOR, 0.5, Qt.PenStyle.DashLine)

    SERIES_COLOR = QColor(255, 255, 255)
    SERIES_UNSAFE_COLOR = QColor(244, 67, 54, SERIES_COLOR.alpha())

    GRADIENT_WIDTH = 2048
    GRADIENT_HEIGHT = 128

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        chart = QChart(backgroundRoundness=0, backgroundVisible=True, margins=QMargins(0, 0, 0, 0))
        chart.legend().hide()

        super().__init__(chart, parent)
        self.api = api

        self.context_menu = CustomContextMenu(self, self.api)
        self.setMaximumHeight(512)

        # Series (unsafe zones are added first to render in the background)
        self.unsafe_left_line = QLineSeries(self)
        self.unsafe_left_series = QAreaSeries(self.unsafe_left_line)
        self.unsafe_left_series.setBrush(self.UNSAFE_LEFT_COLOR)
        self.unsafe_left_series.setPen(self.UNSAFE_PEN)
        self.chart().addSeries(self.unsafe_left_series)

        self.unsafe_left_line_y = QLineSeries(self)
        self.unsafe_left_series_y = QAreaSeries(self.unsafe_left_line_y)
        self.unsafe_left_series_y.setBrush(self.UNSAFE_LEFT_Y_COLOR)
        self.unsafe_left_series_y.setPen(self.UNSAFE_PEN)
        self.chart().addSeries(self.unsafe_left_series_y)

        self.unsafe_right_line = QLineSeries(self)
        self.unsafe_right_series = QAreaSeries(self.unsafe_right_line)
        self.unsafe_right_series.setBrush(self.UNSAFE_RIGHT_COLOR)
        self.unsafe_right_series.setPen(self.UNSAFE_PEN)
        self.chart().addSeries(self.unsafe_right_series)

        # Main histogram series
        self.line_series = QLineSeries(self)
        self.line_series.setPen(QPen(self.SERIES_COLOR, 1.0))
        self.area_series = QAreaSeries(self.line_series)
        self.area_series.setPen(self.AREA_SERIES_PEN)
        self.area_series.setBrush(self.SERIES_COLOR)
        self.chart().addSeries(self.area_series)

        # Axes
        self.axis_x = QValueAxis(self, tickCount=3, labelFormat="%d")
        self.axis_x.setLabelsColor(self.palette().buttonText().color())

        self.axis_y = QValueAxis(self, tickCount=3)
        self.axis_y.setLabelsVisible(False)

        self.chart().addAxis(self.axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart().addAxis(self.axis_y, Qt.AlignmentFlag.AlignLeft)

        # Attach axes to all series
        self.unsafe_left_series.attachAxis(self.axis_x)
        self.unsafe_left_series_y.attachAxis(self.axis_x)
        self.unsafe_left_series.attachAxis(self.axis_y)
        self.unsafe_left_series_y.attachAxis(self.axis_y)
        self.unsafe_right_series.attachAxis(self.axis_x)
        self.unsafe_right_series.attachAxis(self.axis_y)
        self.area_series.attachAxis(self.axis_x)
        self.area_series.attachAxis(self.axis_y)

        # Apply grid pen style
        self.axis_x.setGridLinePen(self.GRID_PEN)
        self.axis_y.setGridLinePen(self.GRID_PEN)

        # State fields
        self.safe_min: float | None = None
        self.safe_max: float | None = None
        self.max_val = 255.0
        self.show_unsafe = True
        self.is_float = False
        self.is_chroma = False
        self.is_y = False
        self._dithered_brush: QBrush | None = None

        self.chart().plotAreaChanged.connect(self.update_brush_transform)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_brush_transform()

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    def update_brush_transform(self, rect: QRectF | None = None) -> None:
        if not isinstance(rect, QRectF):
            rect = self.chart().plotArea()

        if rect.width() > 0 and rect.height() > 0 and self._dithered_brush:
            self._dithered_brush.setTransform(
                QTransform().translate(rect.left(), rect.top()).scale(rect.width() / self.GRADIENT_WIDTH, 1.0)
            )
            self.chart().setPlotAreaBackgroundBrush(self._dithered_brush)

    def set_gradient_background(self, color_family: vs.ColorFamily, plane_idx: int) -> None:
        match color_family, plane_idx:
            case vs.RGB, 0:  # R: Black -> Red
                stops = [
                    (0.0, QColor(64, 0, 0, 128)),
                    (1.0, QColor(255, 0, 0, 192)),
                ]
            case vs.RGB, 1:  # G: Black -> Green
                stops = [
                    (0.0, QColor(0, 64, 0, 128)),
                    (1.0, QColor(0, 255, 0, 192)),
                ]
            case vs.RGB, 2:  # B: Black -> Blue
                stops = [
                    (0.0, QColor(0, 0, 64, 128)),
                    (1.0, QColor(0, 0, 255, 192)),
                ]
            case (vs.YUV, 0) | (vs.GRAY, _):  # Y (Luma): Black -> White
                stops = [
                    (0.0, QColor(16, 16, 16, 64)),
                    (1.0, QColor(255, 255, 255, 128)),
                ]
            case vs.YUV, 1:  # U (Chroma)
                stops = [
                    (0.0, QColor(128, 152, 0, 128)),
                    (0.5, QColor(128, 128, 128, 128)),
                    (1.0, QColor(128, 104, 255, 128)),
                ]
            case vs.YUV, 2:  # V (Chroma)
                stops = [
                    (0.0, QColor(0, 188, 128, 128)),
                    (0.5, QColor(128, 128, 128, 128)),
                    (1.0, QColor(255, 68, 128, 128)),
                ]
            case _:
                raise NotImplementedError

        self._dithered_brush = create_dithered_brush(stops, self.GRADIENT_WIDTH, self.GRADIENT_HEIGHT)
        self.update_brush_transform()
        self.chart().setPlotAreaBackgroundVisible(True)
        self.chart().setBackgroundBrush(Qt.GlobalColor.transparent)

    def configure(self, fmt: vs._VideoFormatDict, plane_idx: int, show_unsafe: bool) -> None:
        self.is_float = fmt["sample_type"] == vs.FLOAT
        self.is_chroma = fmt["color_family"] == vs.YUV and plane_idx > 0
        self.is_y = fmt["color_family"] in (vs.YUV, vs.GRAY) and plane_idx == 0
        self.show_unsafe = show_unsafe

        # Configure background gradient
        self.set_gradient_background(fmt["color_family"], plane_idx)

        # Calculate safe ranges and max_val
        if fmt["color_family"] == vs.RGB:
            self.safe_min = None
            self.safe_max = None
        else:
            if fmt["sample_type"] == vs.INTEGER:
                scale_shift = fmt["bits_per_sample"] - 8
                self.safe_min = 16 << scale_shift
                self.safe_max = 235 << scale_shift if plane_idx == 0 else 240 << scale_shift
            else:
                self.safe_min = 4096
                self.safe_max = 60160 if plane_idx == 0 else 61440

        self.max_val = 1 << fmt["bits_per_sample"] if fmt["sample_type"] == vs.INTEGER else 1024

    def update_data(self, hist: npt.NDArray[np.intp], target_w: int, setting_val: int) -> None:
        N = len(hist)  # noqa: N806

        if N > 256:
            self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        else:
            self.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Calculate target bin count and downsampling step size
        target_w = min(setting_val, N) if setting_val > 0 else max(64, target_w)

        step = 1
        while N // step > target_w:
            step *= 2

        M = N // step  # noqa: N806

        # Downsample using Maximum value pooling
        downsampled = (hist.reshape(M, step).max(axis=1) if step > 1 else hist).astype(np.float64)
        # Setup X-axis coordinates centered on the downsampled bin ranges
        x_coords = np.arange(M, dtype=np.float64) * step + (step - 1) / 2.0
        if self.is_float:
            x_coords /= N - 1
            if self.is_chroma:
                x_coords -= 0.5
        self.line_series.replaceNp(x_coords, downsampled)  # type: ignore[arg-type]

        # Update axis ranges
        if self.is_float:
            if self.is_chroma:
                self.axis_x.setRange(-0.5, 0.5)
            else:
                self.axis_x.setRange(0.0, 1.0)
            self.axis_x.setLabelFormat("%.1f")
        else:
            self.axis_x.setRange(0, N - 1)
            self.axis_x.setLabelFormat("%d")
        self.axis_y.setRange(0, max_y if (max_y := downsampled.max()) > 0 else 1.0)

        # Update unsafe zones shading if active
        if self.show_unsafe and self.safe_min is not None and self.safe_max is not None:
            if self.is_y:
                self.unsafe_left_series_y.setVisible(True)
                self.unsafe_left_series.setVisible(False)
            else:
                self.unsafe_left_series_y.setVisible(False)
                self.unsafe_left_series.setVisible(True)

            self.unsafe_right_series.setVisible(True)

            axis_max_y = self.axis_y.max()
            if self.is_float:
                if self.is_chroma:
                    safe_min_val = self.safe_min / (N - 1) - 0.5
                    safe_max_val = self.safe_max / (N - 1) - 0.5
                    self.unsafe_left_line.replace([QPointF(-0.5, axis_max_y), QPointF(safe_min_val, axis_max_y)])
                    self.unsafe_left_line_y.replace([QPointF(-0.5, axis_max_y), QPointF(safe_min_val, axis_max_y)])
                    self.unsafe_right_line.replace([QPointF(safe_max_val, axis_max_y), QPointF(0.5, axis_max_y)])
                else:
                    safe_min_val = self.safe_min / (N - 1)
                    safe_max_val = self.safe_max / (N - 1)
                    self.unsafe_left_line.replace([QPointF(0.0, axis_max_y), QPointF(safe_min_val, axis_max_y)])
                    self.unsafe_left_line_y.replace([QPointF(0.0, axis_max_y), QPointF(safe_min_val, axis_max_y)])
                    self.unsafe_right_line.replace([QPointF(safe_max_val, axis_max_y), QPointF(1.0, axis_max_y)])
            else:
                self.unsafe_left_line.replace([QPointF(0, axis_max_y), QPointF(self.safe_min, axis_max_y)])
                self.unsafe_left_line_y.replace([QPointF(0, axis_max_y), QPointF(self.safe_min, axis_max_y)])
                self.unsafe_right_line.replace([QPointF(self.safe_max, axis_max_y), QPointF(self.max_val, axis_max_y)])

            # Apply gradient to highlight unsafe zones on the series
            # ObjectMode maps 0.0 to 1.0 across the plot area (value 0 to N - 1)
            t1_left = clamp((self.safe_min - 1) / (N - 1), 0, 1)
            t1_right = clamp((self.safe_min - 0.9) / (N - 1), 0, 1)
            t2_left = clamp((self.safe_max + 0.9) / (N - 1), 0, 1)
            t2_right = clamp((self.safe_max + 1) / (N - 1), 0, 1)

            gradient = QLinearGradient(0, 0, 1, 0)
            gradient.setCoordinateMode(QGradient.CoordinateMode.ObjectMode)

            gradient.setColorAt(0.0, self.SERIES_UNSAFE_COLOR)
            gradient.setColorAt(t1_left, self.SERIES_UNSAFE_COLOR)
            gradient.setColorAt(t1_right, self.SERIES_COLOR)
            gradient.setColorAt(t2_left, self.SERIES_COLOR)
            gradient.setColorAt(t2_right, self.SERIES_UNSAFE_COLOR)
            gradient.setColorAt(1.0, self.SERIES_UNSAFE_COLOR)

            self.area_series.setBrush(gradient)
            self.line_series.setPen(QPen(gradient, 1))
        else:
            self.unsafe_left_series.setVisible(False)
            self.unsafe_left_series_y.setVisible(False)
            self.unsafe_right_series.setVisible(False)
            self.area_series.setBrush(self.SERIES_COLOR)
            self.line_series.setPen(QPen(self.SERIES_COLOR, 1))


def create_dithered_brush(stops: Sequence[tuple[float, QColor]], width: int, height: int) -> QBrush:
    t = np.linspace(0.0, 1.0, width, dtype=np.float32)
    xp = [stop[0] for stop in stops]

    r_stops = [stop[1].red() for stop in stops]
    g_stops = [stop[1].green() for stop in stops]
    b_stops = [stop[1].blue() for stop in stops]
    a_stops = [stop[1].alpha() for stop in stops]

    r_1d = np.interp(t, xp, r_stops)
    g_1d = np.interp(t, xp, g_stops)
    b_1d = np.interp(t, xp, b_stops)
    a_1d = np.interp(t, xp, a_stops)

    # Expand to 2D
    r = np.tile(r_1d, (height, 1))
    g = np.tile(g_1d, (height, 1))
    b = np.tile(b_1d, (height, 1))
    a = np.tile(a_1d, (height, 1))

    noise = np.random.uniform(-2.0, 2.0, size=(height, width))

    r_dithered = (r + noise).clip(0, 255).astype(np.uint8)
    g_dithered = (g + noise).clip(0, 255).astype(np.uint8)
    b_dithered = (b + noise).clip(0, 255).astype(np.uint8)
    a_dithered = (a + noise).clip(0, 255).astype(np.uint8)

    rgba = np.dstack([r_dithered, g_dithered, b_dithered, a_dithered])
    img = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()  # type: ignore[call-overload]
    return QBrush(img)
