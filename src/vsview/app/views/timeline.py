from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from logging import getLogger
from math import floor
from typing import Any, Literal, NamedTuple, Self

from jetpytools import clamp, cround
from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTime, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QCursor,
    QFocusEvent,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
    QRgba64,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QToolTip,
    QWidget,
    QWidgetAction,
)
from vsengine.loops import get_loop

from ...assets import IconName, IconReloadMixin
from ...vsenv import run_in_loop
from ..outputs import AudioOutput
from ..settings import SettingsManager
from .components import SegmentedControl

logger = getLogger(__name__)


class Frame(int):
    """Frame number type."""


class Time(timedelta):
    """Time type."""

    def to_qtime(self) -> QTime:
        """Convert a Time object to a QTime object."""
        # QTime expects milliseconds since the start of the day
        total_ms = cround(self.total_seconds() * 1000)

        # Caps at 23:59:59.999. If delta > 24h, it wraps around.
        return QTime.fromMSecsSinceStartOfDay(total_ms)

    def to_ts(self, fmt: str) -> str:
        """
        Formats a timedelta object using standard Python formatting syntax.

        Available keys:
        {D}  : Days
        {H}  : Hours (0-23)
        {M}  : Minutes (0-59)
        {S}  : Seconds (0-59)
        {ms} : Milliseconds (0-999)
        {us} : Microseconds (0-999999)

        Total duration keys:
        {th} : Total Hours (e.g., 100 hours)
        {tm} : Total Minutes
        {ts} : Total Seconds

        Example:
            ```python
            # 1. Standard Clock format (Padding with :02d)
            # Output: "26:05:03"
            print(time.to_ts(td, "{th:02d}:{M:02d}:{S:02d}"))

            # 2. Detailed format
            # Output: "1 days, 02 hours, 05 minutes"
            print(time.to_ts(td, "{D} days, {H:02d} hours, {M:02d} minutes"))

            # 3. With Milliseconds
            # Output: "02:05:03.500"
            print(time.to_ts(td, "{H:02d}:{M:02d}:{S:02d}.{ms:03d}"))
            ```

        """
        total_seconds = int(self.total_seconds())

        days = self.days
        hours, remainder = divmod(self.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        milliseconds = self.microseconds // 1000

        format_data = {
            "D": days,
            "H": hours,
            "M": minutes,
            "S": seconds,
            "ms": milliseconds,
            "us": self.microseconds,
            # Total durations (useful for "26 hours ago")
            "th": total_seconds // 3600,
            "tm": total_seconds // 60,
            "ts": total_seconds,
        }

        return fmt.format(**format_data)

    @classmethod
    def from_qtime(cls, qtime: QTime) -> Self:
        """Convert a QTime object to a Time object."""
        return cls(milliseconds=qtime.msecsSinceStartOfDay())


@cache
def generate_label_format(notch_interval_t: Time, end_time: Time) -> str:
    if end_time >= Time(hours=1):
        return "{H}:{M:02d}:{S:02d}"

    if notch_interval_t >= Time(minutes=1):
        return "{M}:{S:02d}"

    if end_time > Time(seconds=10):
        return "{M}:{S:02d}"

    return "{S}.{ms:03d}"


class Notch[T: (Time, Frame)]:
    """Represents a notch marker on the timeline."""

    class CacheKey(NamedTuple):
        rect: QRectF
        total_frames: int

    class CacheValue[T0: (Time, Frame)](NamedTuple):
        scroll_rect: QRectF
        labels_notches: list[Notch[T0]]
        rects_to_draw: list[tuple[QRectF, str]]

    class CacheEntry[T1: (Time, Frame)](NamedTuple):
        key: Notch.CacheKey
        value: Notch.CacheValue[T1]

    def __init__(
        self,
        data: T,
        end_data: T | None = None,
        color: Qt.GlobalColor | QColor | QRgba64 | str | int | None = None,
        line: QLineF | None = None,
        end_line: QLineF | None = None,
        label: str = "",
    ) -> None:
        self.data: T = data
        self.end_data: T | None = end_data
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.line = line if line is not None else QLineF()
        self.end_line = end_line
        self.label = label

    def __hash__(self) -> int:
        return hash((self.data, self.end_data))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Notch):
            return NotImplemented
        return (self.data, self.end_data) == (other.data, other.end_data)

    def draw(self, painter: QPainter, scroll_rect: QRectF, range_alpha: int = 80, cosmetic: bool = False) -> None:
        pen = QPen(self.color, 1)

        if cosmetic:
            pen.setCosmetic(True)

        painter.setPen(pen)

        if self.end_line is not None:
            x1, x2 = self.line.x1(), self.end_line.x1()
            fill_color = QColor(self.color)
            fill_color.setAlpha(range_alpha)
            painter.fillRect(QRectF(min(x1, x2), scroll_rect.top(), abs(x2 - x1), scroll_rect.height()), fill_color)
            painter.drawLine(self.line)
            painter.drawLine(self.end_line)
        else:
            painter.drawLine(self.line)


class HoverLabel(NamedTuple):
    text: str
    x: float
    color: QColor
    y_offset: float = 0.0


class TimelineHoverPopup(QWidget):
    # Constants
    NUM_LAYERS = 3
    POPUP_CORNER_RADIUS = 5
    PILL_CORNER_RADIUS = 4
    LABEL_PADDING_H = 4
    LABEL_PADDING_V = 2
    LABEL_STAGGER_OFFSETS = (0, -20, -40)
    LABEL_SPACING = 10
    ZOOMED_NOTCH_SPACING = 75
    RANGE_FILL_ALPHA = 80

    def __init__(self, parent: Timeline) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._timeline = parent
        self.radius = 50
        self.zoom_factor = 4.0
        self.hover_x = -1

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.hover_x < 0:
            return

        popup_rect = QRectF(self.rect())

        with QPainter(self) as painter:
            self._draw_background(painter, popup_rect)

            # Setup clipping and base transform
            painter.setClipRect(popup_rect)
            y_offset = self.height() - self._timeline.height()
            painter.translate(self.radius, y_offset)
            painter.scale(self.zoom_factor, 1.0)
            painter.translate(-self.hover_x, 0)

            self._draw_zoomed_notches(painter)
            self._draw_scroll_bar(painter)
            self._draw_custom_notches(painter, popup_rect, y_offset)
            self._draw_indicators(painter)

    def update_state(self, hover_x: int) -> None:
        self.zoom_factor = SettingsManager.global_settings.timeline.hover_zoom_factor
        self.radius = SettingsManager.global_settings.timeline.hover_zoom_radius
        self.hover_x = hover_x

        popup_width = self.radius * 2
        popup_height = round(self._timeline.height() * (1 + 0.33 * self.NUM_LAYERS))

        global_pos = self._timeline.mapToGlobal(QPoint(hover_x, 0))

        # Position popup above the timeline
        x = global_pos.x() - self.radius
        y = global_pos.y() - popup_height - 10

        self.setGeometry(x, y, popup_width, popup_height)
        self.update()

    def _draw_background(self, painter: QPainter, popup_rect: QRectF) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(popup_rect, self._timeline.palette().color(self._timeline.BACKGROUND_COLOR))
        painter.setPen(QPen(self._timeline.palette().color(self._timeline.TEXT_COLOR), 1))
        painter.drawRoundedRect(popup_rect, self.POPUP_CORNER_RADIUS, self.POPUP_CORNER_RADIUS)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)

    def _draw_zoomed_notches(self, painter: QPainter) -> None:
        # Generate and draw main timeline notches for the zoomed view.
        fm = painter.fontMetrics()

        # Calculate viewport in timeline X coordinates
        half_view_x = self.radius / self.zoom_factor
        view_start_x = self.hover_x - half_view_x
        view_end_x = self.hover_x + half_view_x

        # Target interval in original space
        target_interval_x = self.ZOOMED_NOTCH_SPACING / self.zoom_factor

        tmp_notches = list[tuple[int, str]]()
        if self._timeline.mode == "frame":
            interval_f = self._timeline.calculate_notch_interval_f(int(target_interval_x))
            # Improved accuracy: clamp and extend by 1 to avoid rounding issues
            start_f = max(0, self._timeline.x_to_frame(int(view_start_x)) - 1)
            curr_f = Frame((start_f // interval_f) * interval_f)

            while curr_f < self._timeline.total_frames:
                x = self._timeline.cursor_to_x(curr_f)
                if x > view_end_x + target_interval_x:
                    break

                if x >= view_start_x - target_interval_x:
                    tmp_notches.append((x, str(curr_f)))

                curr_f = Frame(curr_f + interval_f)
        else:
            interval_t = self._timeline.calculate_notch_interval_t(int(target_interval_x))
            start_t = self._timeline.x_to_time(max(0, int(view_start_x)))
            interval_secs = interval_t.total_seconds()

            if interval_secs > 0:
                curr_secs = max(0, (start_t.total_seconds() // interval_secs) * interval_secs)
                curr_t = Time(seconds=curr_secs)
                label_format = generate_label_format(interval_t, self._timeline.total_time)

                while curr_t <= self._timeline.total_time:
                    x = self._timeline.cursor_to_x(curr_t)
                    if x > view_end_x + target_interval_x:
                        break

                    if x >= view_start_x - target_interval_x:
                        tmp_notches.append((x, curr_t.to_ts(label_format)))

                    curr_t = Time(seconds=curr_t.total_seconds() + interval_secs)

        # Draw notches and labels
        notch_pen = QPen(self._timeline.palette().color(self._timeline.TEXT_COLOR), 1)
        notch_pen.setCosmetic(True)
        painter.setPen(notch_pen)

        lnotch_y = self._timeline.rect_f.top() + self._timeline.font_height + self._timeline.notch_height + 5
        lnotch_top = lnotch_y - self._timeline.notch_height

        for nx, ntext in tmp_notches:
            painter.drawLine(QLineF(nx, lnotch_y, nx, lnotch_top))

            # Map to zoomed space for 1:1 text
            zoomed_pos = painter.transform().map(QPointF(nx, lnotch_top))

            painter.save()
            painter.resetTransform()
            painter.setPen(self._timeline.palette().color(self._timeline.TEXT_COLOR))
            text_width = fm.horizontalAdvance(ntext)
            painter.drawText(QPointF(zoomed_pos.x() - text_width / 2, zoomed_pos.y() - fm.descent()), ntext)
            painter.restore()

    def _draw_scroll_bar(self, painter: QPainter) -> None:
        painter.fillRect(self._timeline.scroll_rect, self._timeline.palette().color(self._timeline.SCROLL_BAR_COLOR))

    def _draw_custom_notches(self, painter: QPainter, popup_rect: QRectF, y_offset: float) -> None:
        # Draw provider notches (bookmarks, keyframes) with staggered labels.
        fm = painter.fontMetrics()

        # Collect labels with range support
        all_labels = list[HoverLabel]()
        for provider_notches in self._timeline.notches.values():
            for p_notch in provider_notches:
                if not (label_text := self._format_notch_label(p_notch)):
                    continue

                x_pos = painter.transform().map(QPointF(p_notch.line.x1(), 0)).x()
                all_labels.append(HoverLabel(text=label_text, x=x_pos, color=p_notch.color))

        # Sort and stagger
        all_labels = sorted(all_labels, key=lambda lbl: lbl.x)
        staggered_labels = self._apply_staggering(all_labels, fm)

        # Draw labels with leader lines and pills
        for lbl in staggered_labels:
            self._draw_label_pill(painter, lbl, popup_rect, y_offset, fm)

        # Draw notches and range fills in scaled space
        for provider_notches in self._timeline.notches.values():
            for p_notch in provider_notches:
                p_notch.draw(painter, self._timeline.scroll_rect, self.RANGE_FILL_ALPHA, cosmetic=True)

    def _format_notch_label(self, notch: Notch[Any]) -> str:
        base_label = notch.label

        if notch.end_data is not None:
            # Format: [start, end] label
            start_str = self._format_data_value(notch.data)
            end_str = self._format_data_value(notch.end_data)
            range_prefix = f"[{start_str}, {end_str}]"
            return f"{range_prefix} {base_label}" if base_label else range_prefix

        if base_label:
            # Handle format strings in label
            if "{" in base_label:
                return base_label.format(self._format_data_value(notch.data))
            return base_label
        return ""

    def _format_data_value(self, data: Frame | Time) -> str:
        match data, self._timeline.mode:
            case Time(), "time":
                return data.to_ts("{H:02d}:{M:02d}:{S:02d}.{ms:03d}")
            case Time(), "frame":
                # In frame mode, convert time to frame for display
                return str(self._timeline.x_to_frame(self._timeline.cursor_to_x(data)))
            case _:
                return str(data)

    def _apply_staggering(self, labels: list[HoverLabel], fm: QFontMetrics) -> list[HoverLabel]:
        if not labels:
            return []

        # Apply vertical staggering to overlapping labels.

        staggered = list[HoverLabel]()
        level_index = 0
        last_x = -9999.0

        for lbl in labels:
            if lbl.x - last_x < fm.horizontalAdvance(lbl.text) + self.LABEL_SPACING:
                level_index = (level_index + 1) % len(self.LABEL_STAGGER_OFFSETS)
            else:
                level_index = 0

            staggered.append(lbl._replace(y_offset=self.LABEL_STAGGER_OFFSETS[level_index]))
            last_x = lbl.x

        return staggered

    def _draw_label_pill(
        self,
        painter: QPainter,
        lbl: HoverLabel,
        popup_rect: QRectF,
        y_offset: float,
        fm: QFontMetrics,
    ) -> None:
        # Draw a single label with leader line and pill background.
        text_width = fm.horizontalAdvance(lbl.text)
        text_height = fm.height()

        painter.save()
        painter.resetTransform()

        p_y = self._timeline.scroll_rect.top() + y_offset - text_height - 5 + lbl.y_offset
        t_rect = QRectF(
            lbl.x - text_width / 2 - self.LABEL_PADDING_H,
            p_y - self.LABEL_PADDING_V,
            text_width + self.LABEL_PADDING_H * 2,
            text_height + self.LABEL_PADDING_V * 2,
        )

        if popup_rect.intersects(t_rect):
            # Leader line
            leader_pen = QPen(self._timeline.palette().color(self._timeline.TEXT_COLOR), 1)
            leader_pen.setCosmetic(True)
            painter.setPen(leader_pen)
            painter.setOpacity(0.5)
            painter.drawLine(QPointF(lbl.x, self._timeline.scroll_rect.top()), QPointF(lbl.x, t_rect.bottom()))
            painter.setOpacity(1.0)

            # Pill background
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bg_color = self._timeline.palette().color(self._timeline.BACKGROUND_COLOR)
            painter.setBrush(bg_color)
            painter.setPen(QPen(lbl.color, 1))
            painter.drawRoundedRect(t_rect, self.PILL_CORNER_RADIUS, self.PILL_CORNER_RADIUS)

            # Label text
            painter.setPen(self._timeline.palette().color(self._timeline.TEXT_COLOR))
            painter.drawText(t_rect, Qt.AlignmentFlag.AlignCenter, lbl.text)

        painter.restore()

    def _draw_indicators(self, painter: QPainter) -> None:
        # Draw hover dash and playback cursor.
        hover_pen = QPen(
            self._timeline.palette().color(self._timeline.BACKGROUND_COLOR),
            1,
            Qt.PenStyle.DashLine,
        )
        hover_pen.setCosmetic(True)
        painter.setPen(hover_pen)
        painter.drawLine(
            QLineF(
                self.hover_x,
                self._timeline.scroll_rect.top(),
                self.hover_x,
                self._timeline.scroll_rect.bottom(),
            )
        )

        cursor_pen = QPen(Qt.GlobalColor.black, 1)
        cursor_pen.setCosmetic(True)
        painter.setPen(cursor_pen)
        painter.drawLine(
            QLineF(
                self._timeline.cursor_x,
                self._timeline.scroll_rect.top(),
                self._timeline.cursor_x,
                self._timeline.scroll_rect.bottom(),
            )
        )


class Timeline(QWidget):
    # Signal emits (Frame, Time) when the user clicks on the timeline
    clicked = Signal(object, object)

    # Predefined intervals for frame notches
    NOTCH_INTERVALS_F = tuple(
        Frame(value)
        for value in [1, 5] + [multiplier * (10**power) for power in range(1, 5) for multiplier in (1, 2, 2.5, 5, 7.5)]
    )

    NOTCH_INTERVALS_T = tuple(
        Time(seconds=n) for n in [1, 2, 5, 10, 15, 30, 60, 90, 120, 300, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200]
    )
    MODES = ("frame", "time")

    BACKGROUND_COLOR = QPalette.ColorRole.Window
    TEXT_COLOR = QPalette.ColorRole.WindowText
    SCROLL_BAR_COLOR = QPalette.ColorRole.WindowText

    HOVER_TIME_FORMAT = "{H:02d}:{M:02d}:{S:02d}.{ms:03d}"
    HOVER_PADDING_H = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        SettingsManager.signals.globalChanged.connect(self._on_settings_changed)

        self._mode: Literal["frame", "time"] = SettingsManager.global_settings.timeline.mode

        self.setAutoFillBackground(True)

        self.rect_f = QRectF()
        self.scroll_rect = QRectF()

        # Visual Metrics (scaled by display_scale)
        self.display_scale = SettingsManager.global_settings.timeline.display_scale
        self.notch_interval_target_x = 75
        self.notch_height = 6
        self.font_height = 10
        self.notch_scroll_interval = 2
        self.scroll_height = 10

        self.set_sizes()

        # Internal cursor state (can be Frame, Time, or raw int pixels)
        self._cursor_val: int | Frame | Time = 0

        self.notches = dict[str, set[Notch[Any]]]()

        # Optimization attributes
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)

        # Interaction state
        self.mousepressed = False
        self.hover_x: int | None = None
        self.is_events_blocked = False

        # Initialize cache
        self.notches_cache = self._init_notches_cache()

        # Hover popup
        self.hover_popup = TimelineHoverPopup(self)

        # Context menu for mode switching
        self.context_menu = QMenu(self)

        # Segmented control for mode selection
        self.mode_selector = SegmentedControl(["Frame", "Time"], self)
        self.mode_selector.index = self.MODES.index(self._mode)
        self.mode_selector.segmentChanged.connect(self._on_mode_segment_changed)

        self.mode_selector_action = QWidgetAction(self.context_menu)
        self.mode_selector_action.setDefaultWidget(self.mode_selector)
        self.context_menu.addAction(self.mode_selector_action)

    @property
    def total_frames(self) -> int:
        if isinstance((parent := self.parent()), TimelineControlBar):
            return parent.total_frames

        raise NotImplementedError

    @property
    def total_time(self) -> Time:
        if isinstance((parent := self.parent()), TimelineControlBar):
            return parent.total_time

        raise NotImplementedError

    @property
    def cum_durations(self) -> list[Time] | None:
        if isinstance((parent := self.parent()), TimelineControlBar):
            return parent.cum_durations

        raise NotImplementedError

    @property
    def cursor_x(self) -> int:
        """Returns the X pixel coordinate of the cursor."""
        return self.cursor_to_x(self._cursor_val)

    @cursor_x.setter
    def cursor_x(self, x: int | Frame | Time) -> None:
        """Sets the cursor value (can be int pixel, Frame, or Time), triggering a redraw."""
        self._cursor_val = x
        self.update()

    @property
    def mode(self) -> Literal["frame", "time"]:
        """Current display mode (Frame or Time)."""
        return self._mode

    @mode.setter
    def mode(self, value: Literal["frame", "time"]) -> None:
        """Sets the display mode, triggering a redraw if changed."""
        new_mode = value

        if new_mode == self._mode:
            return

        self._mode = new_mode

        # Update segmented control state
        self.mode_selector.index = self.MODES.index(new_mode)

        self.update()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    def paintEvent(self, event: QPaintEvent) -> None:
        self.rect_f = QRectF(self.rect())

        with QPainter(self) as painter:
            self._draw_widget(painter)

    def _draw_widget(self, painter: QPainter) -> None:
        setup_key = Notch.CacheKey(self.rect_f, self.total_frames)

        # Current cache entry for the current mode (Frame or Time)
        cache_entry = self.notches_cache[self.mode]

        # Unpack value components from the cache
        self.scroll_rect, labels_notches, rects_to_draw = cache_entry.value

        # Check if cache needs regeneration (if size or total frames changed)
        if setup_key != cache_entry.key:
            lnotch_y = self.rect_f.top() + self.font_height + self.notch_height + 5
            lnotch_x = self.rect_f.left()
            lnotch_top = lnotch_y - self.notch_height

            labels_notches = list[Notch[Any]]()
            label_format = ""

            # Generate notches based on mode
            if self.mode == "time":
                max_value_t = self.total_time
                notch_interval_t = self.calculate_notch_interval_t(self.notch_interval_target_x)
                label_format = generate_label_format(notch_interval_t, max_value_t)
                label_notch_t = Time()

                # Generate intermediate notches
                if notch_interval_t > Time(seconds=0):
                    while lnotch_x < self.rect_f.right() and label_notch_t <= max_value_t:
                        labels_notches.append(
                            Notch(deepcopy(label_notch_t), line=QLineF(lnotch_x, lnotch_y, lnotch_x, lnotch_top))
                        )
                        label_notch_t = Time(seconds=label_notch_t.total_seconds() + notch_interval_t.total_seconds())
                        lnotch_x = self.cursor_to_x(label_notch_t)

                # Add the final notch at the very end
                end_notch_t = Notch(
                    max_value_t, line=QLineF(self.rect_f.right() - 1, lnotch_y, self.rect_f.right() - 1, lnotch_top)
                )
                labels_notches.append(end_notch_t)

            elif self.mode == "frame":
                max_value_f = Frame(self.total_frames - 1)
                notch_interval_f = self.calculate_notch_interval_f(self.notch_interval_target_x)
                label_notch_f = Frame(0)

                # Generate intermediate notches
                if notch_interval_f > 0:
                    while lnotch_x < self.rect_f.right() and label_notch_f <= max_value_f:
                        labels_notches.append(
                            Notch(deepcopy(label_notch_f), line=QLineF(lnotch_x, lnotch_y, lnotch_x, lnotch_top))
                        )
                        # Ensure arithmetic results in Frame
                        label_notch_f = Frame(label_notch_f + notch_interval_f)
                        lnotch_x = self.cursor_to_x(label_notch_f)

                # Add the final notch at the very end
                end_notch_f = Notch(
                    max_value_f, line=QLineF(self.rect_f.right() - 1, lnotch_y, self.rect_f.right() - 1, lnotch_top)
                )
                labels_notches.append(end_notch_f)
            else:
                raise NotImplementedError

            # Define the scrollable area rectangle
            self.scroll_rect = QRectF(
                self.rect_f.left(), lnotch_y + self.notch_scroll_interval, self.rect_f.width(), self.scroll_height
            )

            # Generate rectangles for text labels to draw
            rects_to_draw = list[tuple[QRectF, str]]()

            for i, notch in enumerate(labels_notches):
                match self.mode:
                    case "frame":
                        label = str(notch.data)
                    case "time" if isinstance(notch.data, Time):
                        label = notch.data.to_ts(label_format)
                    case _:
                        raise NotImplementedError

                anchor_rect = QRectF(notch.line.x2(), notch.line.y2(), 0, 0)

                # Align labels based on their position (first, last, or middle)
                if i == 0:
                    rect = painter.boundingRect(
                        anchor_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, label
                    )
                elif i == (len(labels_notches) - 1):
                    rect = painter.boundingRect(
                        anchor_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, label
                    )
                elif i == (len(labels_notches) - 2):
                    # Special handling for the second to last notch to prevent overlap with the last one
                    rect = painter.boundingRect(
                        anchor_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, label
                    )

                    last_notch = labels_notches[-1]

                    match self.mode:
                        case "frame":
                            last_label = str(last_notch.data)
                        case "time" if isinstance(last_notch.data, Time):
                            last_label = last_notch.data.to_ts(label_format)
                        case _:
                            raise NotImplementedError

                    anchor_rect_last = QRectF(last_notch.line.x2(), last_notch.line.y2(), 0, 0)
                    last_rect = painter.boundingRect(
                        anchor_rect_last, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, last_label
                    )

                    # If overlap is detected, remove the second to last notch
                    if last_rect.left() - rect.right() < self.notch_interval_target_x / 10:
                        labels_notches.pop(-2)
                        rects_to_draw.append((last_rect, last_label))
                        break
                else:
                    rect = painter.boundingRect(
                        anchor_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, label
                    )

                rects_to_draw.append((rect, label))

            # Update the cache with the new values
            self.notches_cache[self.mode] = Notch.CacheEntry(
                setup_key,
                Notch.CacheValue(self.scroll_rect, labels_notches, rects_to_draw),
            )

        # Define the cursor line position (contained within scroll_rect)
        cursor_line = QLineF(self.cursor_x, self.scroll_rect.top(), self.cursor_x, self.scroll_rect.bottom())

        # DRAWING START

        # Clear background
        painter.fillRect(self.rect_f, self.palette().color(self.BACKGROUND_COLOR))
        painter.setPen(QPen(self.palette().color(self.TEXT_COLOR)))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw text labels
        for txt_rect, text in rects_to_draw:
            painter.drawText(txt_rect, text)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Draw main notch lines
        for notch in labels_notches:
            painter.drawLine(notch.line)

        # Draw scroll bar area
        painter.fillRect(self.scroll_rect, self.palette().color(self.SCROLL_BAR_COLOR))

        # Draw custom notches from providers (e.g. bookmarks, keyframes)
        for provider_notches in self.notches.values():
            for p_notch in provider_notches:
                p_notch.draw(painter, self.scroll_rect)

        # Draw current frame cursor
        painter.setPen(self.palette().color(self.BACKGROUND_COLOR))
        painter.drawLine(cursor_line)

        # Draw hover indicator if mouse is over the widget
        if self.hover_x is not None:
            if self.mode == "frame":
                text = str(self.x_to_frame(self.hover_x))
            else:
                text = self.x_to_time(self.hover_x).to_ts(self.HOVER_TIME_FORMAT)

            painter.setPen(QPen(self.palette().color(self.BACKGROUND_COLOR), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QLineF(self.hover_x, self.scroll_rect.top(), self.hover_x, self.scroll_rect.bottom()))

            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(text)
            text_height = fm.height()

            rect_x = self.hover_x - (text_width / 2) - (self.HOVER_PADDING_H / 2)
            if rect_x < 0:
                rect_x = 0
            elif rect_x + text_width + self.HOVER_PADDING_H > self.rect_f.width():
                rect_x = self.rect_f.width() - text_width - self.HOVER_PADDING_H

            rect_y = self.rect_f.top()

            bg_rect = QRectF(rect_x, rect_y, text_width + self.HOVER_PADDING_H, text_height)
            painter.fillRect(bg_rect, self.palette().color(self.BACKGROUND_COLOR))
            painter.setPen(self.palette().color(self.TEXT_COLOR))
            painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hover_x = None
        self.hover_popup.hide()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.is_events_blocked:
            self.mousepressed = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)

        if self.is_events_blocked:
            return

        # Only left-click triggers scrubbing; right-click is for context menu
        if event.button() != Qt.MouseButton.LeftButton:
            return

        self.mousepressed = True
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)

        if self.is_events_blocked:
            return

        self.hover_x = int(clamp(event.position().x(), 0, self.rect_f.width()))

        if SettingsManager.global_settings.timeline.view_hover_zoom:
            self.hover_popup.update_state(self.hover_x)
            self.hover_popup.show()
        else:
            self.hover_popup.hide()

        self.update()

        if not self.mousepressed:
            return

        pos = event.position()
        # Check if within scroll area
        scroll_rect = self.notches_cache[self.mode].value.scroll_rect

        # Allow clicking a bit above/below for usability
        click_zone = QRectF(scroll_rect)
        click_zone.setTop(click_zone.top() - 10)
        click_zone.setBottom(click_zone.bottom() + 10)

        if click_zone.contains(pos):
            new_x = int(clamp(pos.x(), 0, self.rect_f.width()))

            self._cursor_val = new_x
            self.update()

            self.clicked.emit(self.x_to_frame(new_x), self.x_to_time(new_x))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def set_sizes(self) -> None:
        # Reset cache as sizes have changed
        self.notches_cache = self._init_notches_cache()

        self.notch_interval_target_x = round(75 * self.display_scale)
        self.notch_height = round(6 * self.display_scale)
        self.font_height = round(10 * self.display_scale)
        self.notch_scroll_interval = round(2 * self.display_scale)
        self.scroll_height = round(10 * self.display_scale)

        self.setMinimumWidth(self.notch_interval_target_x)
        self.setFixedHeight(round(33 * self.display_scale))

        font = self.font()
        font.setPixelSize(self.font_height)
        self.setFont(font)

        self.update()

    def calculate_notch_interval_t(self, target_interval_x: int) -> Time:
        margin = 1 + SettingsManager.global_settings.timeline.notches_margin / 100
        target_interval_t = self.x_to_time(target_interval_x)

        for interval in self.NOTCH_INTERVALS_T:
            if target_interval_t < Time(seconds=interval.total_seconds() * margin):
                return interval

        return self.NOTCH_INTERVALS_T[-1]

    def calculate_notch_interval_f(self, target_interval_x: int) -> Frame:
        margin = 1 + SettingsManager.global_settings.timeline.notches_margin / 100
        target_interval_f = self.x_to_frame(target_interval_x)

        for interval in self.NOTCH_INTERVALS_F:
            if target_interval_f < Frame(round(int(interval) * margin)):
                return interval

        return self.NOTCH_INTERVALS_F[-1]

    def x_to_time(self, x: int) -> Time:
        """Converts an X pixel coordinate to a Time value."""
        if self.rect_f.width() == 0:
            return Time()

        if not self.cum_durations:
            return Time()

        return self.cum_durations[frame - 1] if (frame := self.x_to_frame(x)) > 0 else Time()

    def x_to_frame(self, x: int) -> Frame:
        """Converts an X pixel coordinate to a Frame number."""
        if self.rect_f.width() == 0:
            return Frame(0)
        return Frame(round(x / self.rect_f.width() * self.total_frames))

    def cursor_to_x(self, cursor: int | Frame | Time) -> int:
        """
        Convert a cursor value (Time, Frame, or int pixel) to an X pixel coordinate.
        """
        try:
            width = self.rect_f.width()

            if isinstance(cursor, Time):
                if not self.cum_durations:
                    return 0

                return self.cursor_to_x(Frame(bisect_right(self.cum_durations, cursor)))

            if isinstance(cursor, Frame):
                return floor(cursor / self.total_frames * width)

            return cursor

        except (ZeroDivisionError, ValueError):
            return 0

    def add_notch(
        self,
        key: str,
        data: Frame | Time,
        end_data: Frame | Time | None = None,
        color: Qt.GlobalColor | QColor | QRgba64 | str | int = Qt.GlobalColor.black,
        label: str = "",
    ) -> None:
        cursor_x = self.cursor_to_x(data)
        cursor_line = QLineF(
            cursor_x,
            self.scroll_rect.top(),
            cursor_x,
            self.scroll_rect.top() + self.scroll_rect.height() - 1,
        )

        if end_data is not None:
            end_x = self.cursor_to_x(end_data)
            end_line = QLineF(
                end_x,
                self.scroll_rect.top(),
                end_x,
                self.scroll_rect.top() + self.scroll_rect.height() - 1,
            )
        else:
            end_line = None

        self.notches.setdefault(key, set()).add(
            Notch(data, end_data, color, cursor_line, end_line, label)  # pyright: ignore[reportArgumentType]
        )

    def discard_notch(
        self,
        key: str,
        data: Frame | Time,
        end_data: Frame | Time | None = None,
    ) -> None:
        self.notches.get(key, set()).discard(Notch(data, end_data))  # pyright: ignore[reportArgumentType]

    @contextmanager
    def block_events(self) -> Iterator[None]:
        self.is_events_blocked = True
        self.mousepressed = False
        try:
            yield
        finally:
            self.is_events_blocked = False

    def _init_notches_cache(self) -> dict[Literal["frame", "time"], Notch.CacheEntry[Any]]:
        return {
            "frame": Notch.CacheEntry(Notch.CacheKey(QRectF(), -1), Notch.CacheValue(QRectF(), [], [])),
            "time": Notch.CacheEntry(Notch.CacheKey(QRectF(), -1), Notch.CacheValue(QRectF(), [], [])),
        }

    def _on_settings_changed(self) -> None:
        self._mode = SettingsManager.global_settings.timeline.mode
        self.display_scale = SettingsManager.global_settings.timeline.display_scale
        self.notches_cache = self._init_notches_cache()
        self.set_sizes()
        self.update()

    def _on_mode_segment_changed(self, index: int) -> None:
        self.mode = "frame" if index == 0 else "time"


class FrameEdit(QSpinBox):
    frameChanged = Signal(Frame, Frame)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        super().valueChanged.connect(self._on_value_changed)

        self.setMinimum(0)
        self.setKeyboardTracking(False)

        self.old_value = self.value()
        self._pending_max_commit = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._pending_max_commit:
                self._commit_pending()
            super().keyPressEvent(event)
            return

        if event.text().isdigit():
            current_text = self.lineEdit().selectedText()
            line_edit = self.lineEdit()

            if current_text:
                new_text = (
                    line_edit.text()[: line_edit.selectionStart()]
                    + event.text()
                    + line_edit.text()[line_edit.selectionStart() + len(current_text) :]
                )
            else:
                cursor_pos = line_edit.cursorPosition()
                new_text = line_edit.text()[:cursor_pos] + event.text() + line_edit.text()[cursor_pos:]

            if int(new_text) > self.maximum():
                # Cap at maximum instead of blocking, without triggering valueChanged yet
                with QSignalBlocker(self):
                    self.setValue(self.maximum())
                self._pending_max_commit = True
                return

        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        if self._pending_max_commit:
            self._commit_pending()
        super().focusOutEvent(event)

    def _commit_pending(self) -> None:
        self._pending_max_commit = False
        self._on_value_changed(self.value())

    def _on_value_changed(self, value: int) -> None:
        self.frameChanged.emit(self.value(), self.old_value)
        self.old_value = self.value()


class TimeEdit(QTimeEdit):
    valueChanged = Signal(QTime, QTime)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.timeChanged.connect(self._on_time_changed)

        self.setDisplayFormat("H:mm:ss.zzz")
        self.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        self.setMinimumTime(QTime())
        self.setKeyboardTracking(False)

        self.old_time = self.time()

    def _on_time_changed(self, value: QTime) -> None:
        self.valueChanged.emit(self.time(), self.old_time)
        self.old_time = self.time()


class StepSpinBox(QSpinBox):
    def stepBy(self, steps: int) -> None:
        if self.value() == 1 and steps < 0:
            self.setValue(-1)
        elif self.value() == -1 and steps > 0:
            self.setValue(1)
        else:
            super().stepBy(steps)


@dataclass(slots=True, repr=False, eq=False, match_args=False)
class PlaybackSettings:
    seek_step: int = 1
    speed: float = 1.0
    uncapped: bool = False
    zone_frames: int = 100
    loop: bool = False
    step: int = 1


class PlaybackContainer(QWidget, IconReloadMixin):
    ICON_SIZE = QSize(24, 24)
    ICON_COLOR = QPalette.ColorRole.ToolTipText

    settingsChanged = Signal(int, float, bool)  # seek_step, speed, uncapped
    playZone = Signal(int, bool, int)  # zone_frames, loop, int
    volumeChanged = Signal(float)  # volume 0.0-1.0
    muteChanged = Signal(bool)  # is_muted
    audioDelayChanged = Signal(float)  # seconds

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setAutoFillBackground(True)

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setObjectName(self.__class__.__name__)

        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(4, 0, 4, 0)
        self.current_layout.setSpacing(4)

        # Buttons creation
        self.seek_n_back_btn = self._make_button(IconName.REWIND, "Seek N frames backward")
        self.seek_1_back_btn = self._make_button(IconName.SKIP_BACK, "Seek 1 frame backward")

        # Play/Pause uses different icons for each state (not just color)
        self.play_pause_btn = self._make_button(self._make_play_pause_icon(), "Play / Pause", checkable=True)

        self.seek_1_fwd_btn = self._make_button(IconName.SKIP_FORWARD, "Seek 1 frame forward")
        self.seek_n_fwd_btn = self._make_button(IconName.FAST_FORWARD, "Seek N frames forward")

        self.current_layout.addSpacing(5)

        self.time_edit = TimeEdit(self)
        self.current_layout.addWidget(self.time_edit)

        self.frame_edit = FrameEdit(self)
        self.current_layout.addWidget(self.frame_edit)

        self.current_layout.addSpacing(5)

        # Audio controls
        self.audio_controls = QWidget(self)
        self.audio_controls_layout = QHBoxLayout(self.audio_controls)
        self.audio_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_controls_layout.setSpacing(4)

        self.mute_btn = self.make_tool_button(
            self.make_icon((IconName.VOLUME_HIGH, self.palette().color(self.ICON_COLOR)), size=self.ICON_SIZE),
            "Mute / Unmute",
            self.audio_controls,
            checkable=True,
            icon_size=self.ICON_SIZE,
            color_role=self.ICON_COLOR,
        )
        self.mute_btn.clicked.connect(self._on_mute_clicked)
        self.audio_controls_layout.addWidget(self.mute_btn)

        # Volume slider
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self.audio_controls)
        self.volume_slider.setRange(0, 1000)
        self.volume_slider.setValue(int(SettingsManager.global_settings.playback.default_volume * 1000))
        self.volume_slider.setFixedWidth(60)
        self.volume_slider.setToolTip("Volume: 50%")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.audio_controls_layout.addWidget(self.volume_slider)

        self.current_layout.addWidget(self.audio_controls)
        self.audio_controls.setEnabled(False)

        self._is_muted = False
        self._volume = SettingsManager.global_settings.playback.default_volume
        self._audio_delay = SettingsManager.global_settings.playback.audio_delay
        self._update_mute_icon()

        self._setup_context_menu()

        # Different icons per state, use custom callback
        self.register_icon_callback(lambda: self.play_pause_btn.setIcon(self._make_play_pause_icon()))
        self.register_icon_callback(self._update_mute_icon)

    def _make_play_pause_icon(self) -> QIcon:
        palette = self.palette()
        return self.make_icon(
            {
                (QIcon.Mode.Normal, QIcon.State.Off): (
                    IconName.PLAY,
                    palette.color(self.ICON_COLOR),
                ),
                (QIcon.Mode.Normal, QIcon.State.On): (
                    IconName.PAUSE,
                    palette.color(QPalette.ColorRole.Mid),
                ),
            },
            size=self.ICON_SIZE,
        )

    def _make_button(
        self,
        icon: IconName | QIcon,
        tooltip: str,
        *,
        checkable: bool = False,
        color: QColor | None = None,
    ) -> QToolButton:
        btn = self.make_tool_button(
            icon,
            tooltip,
            self,
            checkable=checkable,
            icon_size=self.ICON_SIZE,
            color=color,
            color_role=self.ICON_COLOR,
        )
        self.current_layout.addWidget(btn)
        return btn

    def _setup_context_menu(self) -> None:
        self.context_menu = QMenu(self)

        seek_step_widget = QWidget(self.context_menu)
        seek_step_layout = QHBoxLayout(seek_step_widget)
        seek_step_layout.setContentsMargins(8, 4, 8, 4)

        seek_step_layout.addWidget(QLabel("Seek Step", seek_step_widget))

        self.seek_step_spinbox = QSpinBox(seek_step_widget)
        self.seek_step_spinbox.setMinimum(1)
        self.seek_step_spinbox.setMaximum(1_000_000)
        self.seek_step_spinbox.valueChanged.connect(self._on_seek_step_changed)
        seek_step_layout.addWidget(self.seek_step_spinbox)

        seek_step_action = QWidgetAction(self.context_menu)
        seek_step_action.setDefaultWidget(seek_step_widget)
        self.context_menu.addAction(seek_step_action)

        self.reset_seek_step_to_global_action = self.context_menu.addAction("Reset to Global")
        self.reset_seek_step_to_global_action.triggered.connect(self._on_reset_seek_step)

        self.context_menu.addSeparator()

        speed_widget = QWidget(self.context_menu)
        speed_layout = QFormLayout(speed_widget)
        speed_layout.setContentsMargins(6, 6, 6, 6)

        # Speed slider: 0-100 with 1.0x at center (position 50)
        # Left half (0-50): 0.25x to 1.0x in 0.25 steps
        # Right half (51-100): 1.25x to 4.0x in 0.25 steps
        self.speed_slider = QSlider(Qt.Orientation.Horizontal, speed_widget)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.setValue(50)
        self.speed_slider.setMinimumWidth(100)
        self.speed_slider.setToolTip("1.00x")
        self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        self.speed_slider_max = 8.0

        self.speed_reset_btn = self.make_tool_button(IconName.ARROW_U_TOP_LEFT, "Reset to 1.0x", speed_widget)
        self.speed_reset_btn.clicked.connect(self._on_reset_speed)

        speed_row = QWidget(speed_widget)
        speed_row_layout = QHBoxLayout(speed_row)
        speed_row_layout.setContentsMargins(0, 0, 0, 0)
        speed_row_layout.setSpacing(4)
        speed_row_layout.addWidget(self.speed_slider)
        speed_row_layout.addWidget(self.speed_reset_btn)

        self.uncap_checkbox = QCheckBox("Uncap FPS")
        self.uncap_checkbox.toggled.connect(self._on_uncap_changed)

        uncap_row = QWidget(speed_widget)
        uncap_row_layout = QHBoxLayout(uncap_row)
        uncap_row_layout.setContentsMargins(0, 0, 0, 0)
        uncap_row_layout.addStretch()
        uncap_row_layout.addWidget(self.uncap_checkbox)

        speed_layout.addRow("Speed Limit", speed_row)
        speed_layout.addRow(uncap_row)

        speed_action = QWidgetAction(self.context_menu)
        speed_action.setDefaultWidget(speed_widget)
        self.context_menu.addAction(speed_action)

        self.context_menu.addSeparator()

        # Zone Playback section
        zone_widget = QWidget(self.context_menu)
        zone_layout = QFormLayout(zone_widget)
        zone_layout.setContentsMargins(6, 6, 6, 6)

        # Time edit
        self.zone_time_edit = TimeEdit(zone_widget)
        self.zone_time_edit.valueChanged.connect(self._on_zone_time_changed)

        # Frame count spinbox
        self.zone_frame_spinbox = FrameEdit(zone_widget)
        self.zone_frame_spinbox.setMinimum(1)
        self.zone_frame_spinbox.setMaximum(1_000_000)
        self.zone_frame_spinbox.setFixedWidth(90)
        self.zone_frame_spinbox.frameChanged.connect(self._on_zone_frames_changed)

        # Row with both edits
        zone_edits_row = QWidget(zone_widget)
        zone_edits_layout = QHBoxLayout(zone_edits_row)
        zone_edits_layout.setContentsMargins(0, 0, 0, 0)
        zone_edits_layout.setSpacing(4)
        zone_edits_layout.addWidget(self.zone_time_edit)
        zone_edits_layout.addWidget(self.zone_frame_spinbox)

        # Play zone button
        self.play_zone_btn = self.make_tool_button(IconName.PLAY, "Play Zone", zone_widget)
        self.play_zone_btn.clicked.connect(self._on_play_zone_clicked)

        # Loop checkbox
        self.loop_checkbox = QCheckBox("Loop")
        self.loop_checkbox.toggled.connect(self._on_loop_changed)

        # Step spinbox
        self.step_frame_spinbox = StepSpinBox(zone_widget, minimum=-1_000_000, maximum=1_000_000, value=1)
        self.step_frame_spinbox.setFixedWidth(90)
        self.step_frame_spinbox.setToolTip("Step size (negative values are allowed)")
        self.step_frame_spinbox.valueChanged.connect(self._on_step_changed)

        # Row with play step
        zone_play_step_row = QWidget(zone_widget)
        zone_play_step_layout = QHBoxLayout(zone_play_step_row)
        zone_play_step_layout.setContentsMargins(0, 0, 0, 0)
        zone_play_step_layout.setSpacing(4)
        zone_play_step_layout.addStretch()
        zone_play_step_layout.addWidget(self.step_frame_spinbox)

        # Row with loop and play button
        zone_controls_row = QWidget(zone_widget)
        zone_controls_layout = QHBoxLayout(zone_controls_row)
        zone_controls_layout.setContentsMargins(0, 0, 0, 0)
        zone_controls_layout.setSpacing(4)
        zone_controls_layout.addStretch()
        zone_controls_layout.addWidget(self.play_zone_btn)
        zone_controls_layout.addWidget(self.loop_checkbox)

        zone_layout.addRow("Zone Time/Frame", zone_edits_row)
        zone_layout.addRow("Play Step", zone_play_step_row)
        zone_layout.addRow(zone_controls_row)

        zone_action = QWidgetAction(self.context_menu)
        zone_action.setDefaultWidget(zone_widget)
        self.context_menu.addAction(zone_action)

        self.settings = PlaybackSettings()

        self.context_menu.addSeparator()

        # Audio
        self.audio_widget = QWidget(self.context_menu)
        audio_layout = QFormLayout(self.audio_widget)
        audio_layout.setContentsMargins(6, 6, 6, 6)

        self.audio_output_combo = QComboBox(self.audio_widget)
        audio_layout.addRow("Audio Output", self.audio_output_combo)

        self.audio_delay_combo = QDoubleSpinBox(
            self.audio_widget,
            suffix=" ms",
            decimals=3,
            minimum=-10000,
            maximum=10000,
            value=self.audio_delay * 1000,
        )
        self.audio_delay_combo.valueChanged.connect(self._on_audio_delay_changed)
        audio_delay_layout = QHBoxLayout()
        audio_delay_layout.addWidget(QLabel("Delay", self.audio_widget))
        audio_delay_layout.addWidget(self.audio_delay_combo)
        audio_layout.addRow(audio_delay_layout)

        audio_action = QWidgetAction(self.context_menu)
        audio_action.setDefaultWidget(self.audio_widget)
        self.context_menu.addAction(audio_action)

        self.reset_audio_delay_to_global_action = self.context_menu.addAction("Reset to Global")
        self.reset_audio_delay_to_global_action.triggered.connect(self._on_reset_audio_delay)

    def _update_mute_icon(self) -> None:
        if self._is_muted:
            return self.mute_btn.setIcon(
                self.make_icon(
                    (IconName.VOLUME_MUTE, self.palette().color(QPalette.ColorRole.Mid)),
                    size=self.ICON_SIZE,
                )
            )

        if self._volume == 0:
            icon_name = IconName.VOLUME_OFF
        elif self._volume < 0.33:
            icon_name = IconName.VOLUME_LOW
        elif self._volume < 0.67:
            icon_name = IconName.VOLUME_MID
        else:
            icon_name = IconName.VOLUME_HIGH

        self.mute_btn.setIcon(self.make_icon((icon_name, self.palette().color(self.ICON_COLOR)), size=self.ICON_SIZE))

    @property
    def volume(self) -> float:
        return 0.0 if self._is_muted else self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = clamp(value, 0.0, 1.0)

        with QSignalBlocker(self.volume_slider):
            self.volume_slider.setValue(round(self._volume * 1000))

        self._update_mute_icon()

    @property
    def raw_volume(self) -> float:
        return self._volume

    @property
    def is_muted(self) -> bool:
        return self._is_muted

    @is_muted.setter
    def is_muted(self, value: bool) -> None:
        self._is_muted = value

        with QSignalBlocker(self.mute_btn):
            self.mute_btn.setChecked(value)

        self._update_mute_icon()

    @property
    def audio_delay(self) -> float:
        return self._audio_delay

    @audio_delay.setter
    def audio_delay(self, value: float) -> None:
        if self._audio_delay == value:
            return

        self._audio_delay = value

        with QSignalBlocker(self.audio_delay_combo):
            self.audio_delay_combo.setValue(value * 1000)

        self.audioDelayChanged.emit(value)

    @property
    def cum_durations(self) -> list[Time] | None:
        if isinstance((parent := self.parent()), TimelineControlBar):
            return parent.cum_durations

        raise NotImplementedError

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        with QSignalBlocker(self.seek_step_spinbox):
            self.seek_step_spinbox.setValue(self.settings.seek_step)

        with QSignalBlocker(self.speed_slider):
            self.speed_slider.setValue(self._speed_to_slider(self.settings.speed))
            self.speed_slider.setToolTip(f"{self.settings.speed:.2f}x")

        with QSignalBlocker(self.uncap_checkbox):
            self.uncap_checkbox.setChecked(self.settings.uncapped)

        self.speed_slider.setEnabled(not self.settings.uncapped)
        self.speed_reset_btn.setEnabled(not self.settings.uncapped)

        with QSignalBlocker(self.zone_frame_spinbox):
            self.zone_frame_spinbox.setValue(self.settings.zone_frames)

        if self.cum_durations:
            with QSignalBlocker(self.zone_time_edit):
                self.zone_time_edit.setTime(
                    self.cum_durations[self.settings.zone_frames - 1].to_qtime()
                    if self.settings.zone_frames > 0
                    else QTime()
                )

        with QSignalBlocker(self.step_frame_spinbox):
            self.step_frame_spinbox.setValue(self.settings.step)

        with QSignalBlocker(self.loop_checkbox):
            self.loop_checkbox.setChecked(self.settings.loop)

        self.reset_seek_step_to_global_action.setEnabled(
            self.settings.seek_step != SettingsManager.global_settings.timeline.seek_step
        )
        self.reset_audio_delay_to_global_action.setEnabled(
            self.audio_delay != SettingsManager.global_settings.playback.audio_delay
        )

        self.audio_widget.setEnabled(self.audio_output_combo.count() > 0)

        menu_pos = event.globalPos()
        menu_pos.setY(menu_pos.y() - self.context_menu.sizeHint().height())
        self.context_menu.exec(menu_pos)

    @run_in_loop
    def set_audio_outputs(self, aoutputs: list[AudioOutput], index: int | None = None) -> None:
        with QSignalBlocker(self.audio_output_combo):
            self.audio_output_combo.clear()
            self.audio_output_combo.addItems(
                [
                    f"{a.vs_index}: {a.vs_name or f'Audio {a.vs_index}'} ({a.chanels_layout.pretty_name})"
                    for a in aoutputs
                ]
            )

        if len(aoutputs) > 0:
            self.audio_controls.setEnabled(True)

            if index is not None:
                self.audio_output_combo.setCurrentIndex(index)

            self.audioDelayChanged.emit(self.audio_delay)

    def _on_seek_step_changed(self, value: int) -> None:
        self.settings.seek_step = value
        self.reset_seek_step_to_global_action.setEnabled(value != SettingsManager.global_settings.timeline.seek_step)
        self._emit_settings()

    def _on_reset_seek_step(self) -> None:
        # Update spinbox to show global value
        global_seek = SettingsManager.global_settings.timeline.seek_step
        self.settings.seek_step = global_seek

        with QSignalBlocker(self.seek_step_spinbox):
            self.seek_step_spinbox.setValue(global_seek)

        self._emit_settings()

    def _slider_to_speed(self, slider_val: int) -> float:
        # Slider 0-50 -> 0.25 to 1.0 (steps: 0.25, 0.50, 0.75, 1.00)
        # Slider 51-100 -> 1.25 to max (steps: 1.25, 1.50, ..., max)
        speed = (
            0.25 + slider_val / 50.0 * 0.75
            if slider_val <= 50
            else 1.0 + (slider_val - 50) / 50.0 * (self.speed_slider_max - 1.0)
        )
        return round(speed * 4) / 4

    def _speed_to_slider(self, speed: float) -> int:
        return (
            round(((speed - 0.25) / 0.75) * 50)
            if speed <= 1.0
            else round(50 + ((speed - 1.0) / (self.speed_slider_max - 1.0)) * 50)
        )

    def _on_speed_slider_changed(self, value: int) -> None:
        self.settings.speed = self._slider_to_speed(value)
        speed_text = f"{self.settings.speed:.2f}x"
        self.speed_slider.setToolTip(speed_text)

        QToolTip.showText(QCursor.pos(), speed_text, self.speed_slider)

        self._emit_settings()

    def _on_reset_speed(self) -> None:
        self.settings.speed = 1.0

        with QSignalBlocker(self.speed_slider):
            self.speed_slider.setValue(50)

        self.speed_slider.setToolTip("1.00x")
        self._emit_settings()

    def _on_uncap_changed(self, checked: bool) -> None:
        self.settings.uncapped = checked
        self.speed_slider.setEnabled(not checked)
        self.speed_reset_btn.setEnabled(not checked)
        self._emit_settings()

    def _emit_settings(self) -> None:
        self.settingsChanged.emit(self.settings.seek_step, self.settings.speed, self.settings.uncapped)

    def _on_zone_frames_changed(self, new_frame: int, old_frame: int) -> None:
        self.settings.zone_frames = new_frame

        # Convert frames to time
        if self.cum_durations:
            with QSignalBlocker(self.zone_time_edit):
                self.zone_time_edit.setTime(self.cum_durations[new_frame - 1].to_qtime() if new_frame > 0 else QTime())

    def _on_zone_time_changed(self, new_time: QTime, old_time: QTime) -> None:
        # Convert time to frames
        if self.cum_durations:
            frames = max(1, bisect_right(self.cum_durations, Time.from_qtime(new_time)))

            self.settings.zone_frames = frames

            with QSignalBlocker(self.zone_frame_spinbox):
                self.zone_frame_spinbox.setValue(frames)

    def _on_play_zone_clicked(self) -> None:
        self.playZone.emit(self.settings.zone_frames, self.settings.loop, self.settings.step)
        self.context_menu.close()

    def _on_loop_changed(self, checked: bool) -> None:
        self.settings.loop = checked

    def _on_step_changed(self, value: int) -> None:
        if value == 0:
            new_value = 1 if self.settings.step < 0 else -1

            with QSignalBlocker(self.step_frame_spinbox):
                self.step_frame_spinbox.setValue(new_value)

            self.settings.step = new_value
            return

        self.settings.step = value

    def _on_mute_clicked(self, checked: bool) -> None:
        self._is_muted = checked
        self._update_mute_icon()
        self.muteChanged.emit(self._is_muted)

    def _on_volume_changed(self, value: int) -> None:
        self._volume = value / 1000.0
        volume_text = f"Volume: {self._volume * 100:.0f}%"
        self.volume_slider.setToolTip(volume_text)

        QToolTip.showText(QCursor.pos(), volume_text, self.volume_slider)

        self._update_mute_icon()

        # Unmute if volume is changed while muted
        if self._is_muted and self._volume > 0:
            self._is_muted = False
            self.mute_btn.setChecked(False)
            self.muteChanged.emit(False)

        self.volumeChanged.emit(self._volume)

    def _on_audio_delay_changed(self, value: float) -> None:
        self._audio_delay = value / 1000
        self.reset_audio_delay_to_global_action.setEnabled(
            self._audio_delay != SettingsManager.global_settings.playback.audio_delay
        )
        self.audioDelayChanged.emit(self._audio_delay)

    def _on_reset_audio_delay(self) -> None:
        global_delay = SettingsManager.global_settings.playback.audio_delay
        self._audio_delay = global_delay

        with QSignalBlocker(self.audio_delay_combo):
            self.audio_delay_combo.setValue(global_delay * 1000)

        self.audioDelayChanged.emit(global_delay)


class TimelineControlBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Prevent vertical expansion when window is maximized
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        # Timeline and Playback Controls
        self.timeline_layout = QHBoxLayout(self)
        self.timeline_layout.setContentsMargins(0, 0, 0, 0)
        self.timeline_layout.setSpacing(0)

        # Playback Controls
        self.playback_container = PlaybackContainer(self)

        self.timeline_layout.addWidget(self.playback_container)

        self.timeline = Timeline(self)
        self.timeline_layout.addWidget(self.timeline)

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def total_time(self) -> Time:
        return self._cum_durations[-1] if self._cum_durations else Time()

    @property
    def cum_durations(self) -> list[Time] | None:
        return self._cum_durations

    def set_data(self, total_frames: int, cum_durations: list[float] | None = None) -> None:
        self._total_frames = total_frames
        self._cum_durations = [Time(seconds=cum) for cum in cum_durations] if cum_durations else None

        self.timeline.update()

        # Playback Container
        with QSignalBlocker(self.playback_container.zone_frame_spinbox):
            self.playback_container.zone_frame_spinbox.setMaximum(Frame(total_frames - 1))

        with QSignalBlocker(self.playback_container.zone_time_edit):
            self.playback_container.zone_time_edit.setMaximumTime(self.total_time.to_qtime())

        with QSignalBlocker(self.playback_container.frame_edit):
            self.playback_container.frame_edit.setMaximum(Frame(total_frames - 1))

        with QSignalBlocker(self.playback_container.time_edit):
            self.playback_container.time_edit.setMaximumTime(self.total_time.to_qtime())

    @run_in_loop(return_future=False)
    def set_playback_controls_enabled(self, enabled: bool) -> None:
        """
        Enable or disable playback controls (except play/pause button).

        During playback, seek buttons and time/frame edits should be disabled,
        but the play/pause button must remain clickable so users can stop playback.
        """
        self.playback_container.seek_n_back_btn.setEnabled(enabled)
        self.playback_container.seek_1_back_btn.setEnabled(enabled)
        self.playback_container.seek_1_fwd_btn.setEnabled(enabled)
        self.playback_container.seek_n_fwd_btn.setEnabled(enabled)
        self.playback_container.time_edit.setEnabled(enabled)
        self.playback_container.frame_edit.setEnabled(enabled)

    @contextmanager
    def disabled(self) -> Iterator[None]:
        """
        Context manager to disable the control bar and block timeline events.

        Disables the widget, blocks mouse events on the timeline, and re-enables on exit.
        If an exception occurs, the toolbar stays disabled.

        Use for single-frame renders, not for continuous playback (use set_playback_controls_enabled instead).
        """
        loop = get_loop()

        @run_in_loop(return_future=False)
        def disable() -> None:
            self.timeline.hover_popup.hide()
            self.setEnabled(False)

        disable()

        try:
            with self.timeline.block_events():
                yield
        except BaseException:
            # Keep toolbar disabled on error
            raise
        else:
            loop.from_thread(self.setEnabled, True).result()
