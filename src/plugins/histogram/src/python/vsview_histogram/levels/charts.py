from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from typing import override

import numpy as np
import numpy.typing as npt
import vapoursynth as vs
from jetpytools import clamp
from PySide6.QtCharts import QAreaSeries, QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QEvent, QMargins, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QGradient,
    QHideEvent,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsTextItem, QStyleOptionGraphicsItem, QWidget

from vsview.api import PluginAPI

from ..utils import CustomContextMenu

RNG = np.random.default_rng(seed=333)


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

        self.context_menu = CustomContextMenu(self, self.api, self._capture_chart)
        self.setMaximumHeight(512)
        self.setMouseTracking(True)

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

        cross_pen = self._make_cross_pen()

        self.v_line = QGraphicsLineItem(self.chart())
        self.v_line.setPen(cross_pen)
        self.v_line.setZValue(10)

        self.h_line = QGraphicsLineItem(self.chart())
        self.h_line.setPen(cross_pen)
        self.h_line.setZValue(10)

        self.label = LabelText(self.chart())
        self.label.setZValue(11)

        self.set_overlays_visible(False)

        # State fields
        self.safe_min: float | None = None
        self.safe_max: float | None = None
        self.safe_min_coord: float | None = None
        self.safe_max_coord: float | None = None
        self.max_val = 255.0
        self.show_unsafe = True
        self.is_limited = True
        self.is_float = False
        self.is_chroma = False
        self.is_y = False
        self.plane_name = ""

        self._dithered_brush: QBrush | None = None
        self._downsampled_x: npt.NDArray[np.float64] | None = None
        self._downsampled_y: npt.NDArray[np.float64] | None = None
        self._raw_hist: npt.NDArray[np.intp] | None = None
        self._total_pixels = np.intp(0)
        self._step = 1
        self._scale = 1.0
        self._last_snap_idx = np.intp(-1)
        self._last_hover_pos: QPointF | None = None

        self.chart().plotAreaChanged.connect(self.update_brush_transform)

        self.api.register_on_destroy(create_dithered_brush.cache_clear)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_brush_transform()
        self.set_overlays_visible(False)
        self._last_snap_idx = np.intp(-1)
        self._last_hover_pos = None

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        self.set_overlays_visible(False)
        self._last_snap_idx = np.intp(-1)
        self._last_hover_pos = None
        super().hideEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self.set_overlays_visible(False)
        self._last_snap_idx = np.intp(-1)
        self._last_hover_pos = None
        super().leaveEvent(event)

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.axis_x.setLabelsColor(self.palette().buttonText().color())
            cross_pen = self._make_cross_pen()
            self.v_line.setPen(cross_pen)
            self.h_line.setPen(cross_pen)
            if self._last_hover_pos is not None:
                self._last_snap_idx = np.intp(-1)
                self._update_crosshair(self._last_hover_pos)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._last_hover_pos = event.position()
        self._update_crosshair(self._last_hover_pos)
        super().mouseMoveEvent(event)

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
                stops = (
                    (0.0, HashableQColor(64, 0, 0, 128)),
                    (1.0, HashableQColor(255, 0, 0, 192)),
                )
            case vs.RGB, 1:  # G: Black -> Green
                stops = (
                    (0.0, HashableQColor(0, 64, 0, 128)),
                    (1.0, HashableQColor(0, 255, 0, 192)),
                )
            case vs.RGB, 2:  # B: Black -> Blue
                stops = (
                    (0.0, HashableQColor(0, 0, 64, 128)),
                    (1.0, HashableQColor(0, 0, 255, 192)),
                )
            case (vs.YUV, 0) | (vs.GRAY, _):  # Y (Luma): Black -> White
                stops = (
                    (0.0, HashableQColor(16, 16, 16, 64)),
                    (1.0, HashableQColor(255, 255, 255, 128)),
                )
            case vs.YUV, 1:  # U (Chroma)
                stops = (
                    (0.0, HashableQColor(128, 152, 0, 128)),
                    (0.5, HashableQColor(128, 128, 128, 128)),
                    (1.0, HashableQColor(128, 104, 255, 128)),
                )
            case vs.YUV, 2:  # V (Chroma)
                stops = (
                    (0.0, HashableQColor(0, 188, 128, 128)),
                    (0.5, HashableQColor(128, 128, 128, 128)),
                    (1.0, HashableQColor(255, 68, 128, 128)),
                )
            case _:
                raise NotImplementedError

        self._dithered_brush = create_dithered_brush(stops, self.GRADIENT_WIDTH, self.GRADIENT_HEIGHT)
        self.update_brush_transform()
        self.chart().setPlotAreaBackgroundVisible(True)
        self.chart().setBackgroundBrush(Qt.GlobalColor.transparent)

    def configure(self, fmt: vs._VideoFormatDict, plane_idx: int, show_unsafe: bool, is_limited: bool = True) -> None:
        self.is_float = fmt["sample_type"] == vs.FLOAT
        self.is_chroma = fmt["color_family"] == vs.YUV and plane_idx > 0
        self.is_y = fmt["color_family"] in (vs.YUV, vs.GRAY) and plane_idx == 0
        self.show_unsafe = show_unsafe
        self.is_limited = is_limited

        # Configure background gradient
        self.set_gradient_background(fmt["color_family"], plane_idx)

        # Calculate safe ranges and max_val
        if fmt["color_family"] in (vs.YUV, vs.GRAY):
            if fmt["sample_type"] == vs.INTEGER:
                scale_shift = fmt["bits_per_sample"] - 8
                self.safe_min = 16 << scale_shift
                self.safe_max = 235 << scale_shift if plane_idx == 0 else 240 << scale_shift
            else:
                self.safe_min = 4096
                self.safe_max = 60160 if plane_idx == 0 else 61440
        else:
            self.safe_min = None
            self.safe_max = None

        if fmt["color_family"] == vs.GRAY:
            self.plane_name = "Y"
        else:
            self.plane_name = fmt["color_family"].name[plane_idx]

        self.max_val = 1 << fmt["bits_per_sample"] if fmt["sample_type"] == vs.INTEGER else 1024

    def update_data(
        self,
        raw_hist: npt.NDArray[np.intp],
        target_w: int,
        setting_val: int,
        clamp_factor: float = 100.0,
    ) -> None:
        N = len(raw_hist)  # noqa: N806

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

        if step > 1:
            reshaped = raw_hist.reshape(M, step)
            # Sum the sub-bins so bucket weights stay consistent across bit depths.
            # X gets pinned to the tallest sub-bin, so spikes at the edges (0, N-1) and comb peaks line up exactly.
            idx = reshaped.argmax(axis=1)
            downsampled = reshaped.sum(axis=1, dtype=np.float64)
            x_coords = np.arange(M, dtype=np.float64) * step + np.where(downsampled > 0, idx, (step - 1) / 2.0)
        else:
            downsampled = raw_hist.astype(np.float64)
            x_coords = np.arange(M, dtype=np.float64)

        # Clamp after downsampling so the cap applies to what you actually see on screen,
        # whether the raw histogram has 256 or 65536 bins.
        if (total_pixels := raw_hist.sum()) > 0 and clamp_factor < 100.0:
            downsampled = downsampled.clip(0.0, max(1.0, total_pixels * clamp_factor / 100.0))

        if self.is_float:
            # Float planes get digitized into [0, N-1], with limited-range YUV mapped to [safe_min, safe_max].
            # Here we scale back to the native float range ([0, 1], or [-0.5, 0.5] for chroma).
            low = self.safe_min if (self.is_limited and self.safe_min is not None) else 0.0
            high = self.safe_max if (self.is_limited and self.safe_max is not None) else N - 1
            scale = high - low
            offset = 0.5 if self.is_chroma else 0.0

            x_coords = (x_coords - low) / scale - offset
            x_min = -low / scale - offset
            x_max = (N - 1 - low) / scale - offset
        else:
            low = 0.0
            scale = 1.0
            offset = 0.0
            x_min = 0.0
            x_max = N - 1

        self.line_series.replaceNp(x_coords, downsampled)  # type: ignore[arg-type]

        self._raw_hist = raw_hist
        self._total_pixels = total_pixels
        self._downsampled_x = x_coords
        self._downsampled_y = downsampled
        self._step = step
        self._scale = scale
        self._last_snap_idx = np.intp(-1)

        if self.is_float:
            self.axis_x.setRange(x_min, x_max)
            self.axis_x.setTickAnchor(0.0)
            self.axis_x.setTickInterval(0.5)
            self.axis_x.setTickType(QValueAxis.TickType.TicksDynamic)
            self.axis_x.setLabelFormat("%.1f")
        else:
            self.axis_x.setTickType(QValueAxis.TickType.TicksFixed)
            self.axis_x.setRange(x_min, x_max)
            self.axis_x.setLabelFormat("%d")
        self.axis_y.setRange(0, max_y if (max_y := downsampled.max()) > 0 else 1.0)

        if self.safe_min is not None and self.safe_max is not None:
            self.safe_min_coord = (self.safe_min - low) / scale - offset
            self.safe_max_coord = (self.safe_max - low) / scale - offset
        else:
            self.safe_min_coord = None
            self.safe_max_coord = None

        # Update unsafe zones shading if active
        if (
            self.show_unsafe
            and self.safe_min is not None
            and self.safe_max is not None
            and self.safe_min_coord is not None
            and self.safe_max_coord is not None
        ):
            if self.is_y:
                self.unsafe_left_series_y.setVisible(True)
                self.unsafe_left_series.setVisible(False)
            else:
                self.unsafe_left_series_y.setVisible(False)
                self.unsafe_left_series.setVisible(True)

            self.unsafe_right_series.setVisible(True)

            axis_max_y = self.axis_y.max()

            self.unsafe_left_line.replace([QPointF(x_min, axis_max_y), QPointF(self.safe_min_coord, axis_max_y)])
            self.unsafe_left_line_y.replace([QPointF(x_min, axis_max_y), QPointF(self.safe_min_coord, axis_max_y)])
            self.unsafe_right_line.replace([QPointF(self.safe_max_coord, axis_max_y), QPointF(x_max, axis_max_y)])

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

        if self._last_hover_pos is not None and self.underMouse():
            self._update_crosshair(self._last_hover_pos)
        else:
            self._last_hover_pos = None
            self.set_overlays_visible(False)

    def set_overlays_visible(self, visible: bool) -> None:
        self.v_line.setVisible(visible)
        self.h_line.setVisible(visible)
        self.label.setVisible(visible)

    def _make_cross_pen(self) -> QPen:
        cross_color = self.palette().color(QPalette.ColorRole.ToolTipText)
        cross_color.setAlphaF(0.8)
        return QPen(cross_color, 1, Qt.PenStyle.DashLine)

    def _update_crosshair(self, pos: QPointF) -> None:
        area = self.chart().plotArea()

        if (
            not area.contains(pos)
            or self._downsampled_x is None
            or self._downsampled_y is None
            or self._raw_hist is None
            or self._total_pixels == 0
        ):
            self.set_overlays_visible(False)
            return

        val = self.chart().mapToValue(pos, self.area_series)

        idx = np.searchsorted(self._downsampled_x, val.x())
        idx = np.clip(idx, 0, len(self._downsampled_x) - 1)
        if idx > 0 and abs(self._downsampled_x[idx - 1] - val.x()) < abs(self._downsampled_x[idx] - val.x()):
            idx -= 1

        if idx == self._last_snap_idx and self.v_line.isVisible():
            return

        self._last_snap_idx = idx

        snap_x = self._downsampled_x[idx]
        snap_y = self._downsampled_y[idx]

        point = self.chart().mapToPosition(QPointF(snap_x, snap_y), self.area_series)

        self.v_line.setLine(point.x(), area.top(), point.x(), area.bottom())
        self.h_line.setLine(area.left(), point.y(), area.right(), point.y())

        if self._step == 1:
            bin_count = int(self._raw_hist[idx])
        else:
            start_raw = idx * self._step
            end_raw = min(len(self._raw_hist), (idx + 1) * self._step)
            bin_count = self._raw_hist[start_raw:end_raw].sum()

        pct = bin_count / self._total_pixels * 100.0

        if self.is_float:
            val_str = f"{snap_x:.4f}"
        elif self._step == 1:
            val_str = f"{round(snap_x)}"
        else:
            start_val = idx * self._step
            end_val = min(len(self._raw_hist) - 1, (idx + 1) * self._step - 1)
            val_str = f"{start_val} - {end_val}"

        lines = [
            f"<b>{self.plane_name}:</b> {val_str}",
            f"<b>Weight:</b> {bin_count:,} ({pct:.2f}%)",
        ]

        half_bin = 0.0 if snap_y > 0 else (self._step - 1) / (2.0 * self._scale)
        if (
            self.show_unsafe
            and self.safe_min_coord is not None
            and self.safe_max_coord is not None
            and (snap_x + half_bin < self.safe_min_coord or snap_x - half_bin > self.safe_max_coord)
        ):
            lines.append(
                f"<span style='color: {self.SERIES_UNSAFE_COLOR.name()};'><b>[Unsafe Broadcast Range]</b></span>"
            )

        self.label.set_html_text("<br>".join(lines), self.palette())

        label_rect = self.label.boundingRect()
        lx = point.x() + 15
        ly = point.y() - label_rect.height() - 10
        if lx + label_rect.width() > area.right():
            lx = point.x() - label_rect.width() - 15
        if ly < area.top():
            ly = point.y() + 15
        lx = clamp(lx, area.left() + 5, max(area.left() + 5, area.right() - label_rect.width() - 5))
        ly = clamp(ly, area.top() + 5, max(area.top() + 5, area.bottom() - label_rect.height() - 5))

        self.label.setPos(lx, ly)
        self.set_overlays_visible(True)

    def _capture_chart(self) -> QPixmap:
        self.set_overlays_visible(False)
        return self.grab()


class LabelText(QGraphicsTextItem):
    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._bg_color = QColor()
        self._border_color = QColor()
        self.document().setDocumentMargin(6.0)

    def set_html_text(self, text: str, palette: QPalette) -> None:
        self._bg_color = palette.color(QPalette.ColorRole.ToolTipBase)
        self._border_color = palette.color(QPalette.ColorRole.Highlight)
        self.setDefaultTextColor(palette.color(QPalette.ColorRole.ToolTipText))
        self.setHtml(text)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(self._bg_color)
        painter.setPen(QPen(self._border_color, 1.0))
        painter.drawRoundedRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0)
        painter.restore()
        super().paint(painter, option, widget or QWidget())


class HashableQColor(QColor):
    @override
    def __hash__(self) -> int:
        return hash((self.red(), self.green(), self.blue(), self.alpha()))


@cache
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

    noise = RNG.uniform(-2.0, 2.0, size=(height, width))

    r_dithered = (r_1d + noise).clip(0, 255).astype(np.uint8)
    g_dithered = (g_1d + noise).clip(0, 255).astype(np.uint8)
    b_dithered = (b_1d + noise).clip(0, 255).astype(np.uint8)
    a_dithered = (a_1d + noise).clip(0, 255).astype(np.uint8)

    rgba = np.dstack([r_dithered, g_dithered, b_dithered, a_dithered])
    img = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    return QBrush(img)
