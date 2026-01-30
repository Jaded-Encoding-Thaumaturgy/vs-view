from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction
from functools import cache
from logging import getLogger
from math import floor
from typing import Any, Literal, NamedTuple, Protocol, Self

from jetpytools import clamp, cround
from PySide6.QtCore import QEvent, QLineF, QRectF, QSignalBlocker, QSize, Qt, QTime, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QCursor,
    QFocusEvent,
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
    """
    Represents a notch marker on the timeline.

    The color attribute is used for custom/provider notches (bookmarks, keyframes, etc.) that need to stand out.

    Main timeline notches use the palette's WindowText color instead.
    """

    def __init__(
        self,
        data: T,
        color: Qt.GlobalColor | QColor | QRgba64 | str | int | None = None,
        line: QLineF | None = None,
        label: str = "",
    ) -> None:
        self.data: T = data
        self.color = QColor(color) if color is not None else QColor(Qt.GlobalColor.black)
        self.line = line if line is not None else QLineF()
        self.label = label


class NotchProvider(Protocol):
    """Protocol defining what a provider of notches must implement."""

    @property
    def is_notches_visible(self) -> bool: ...
    def get_notches(self) -> list[Notch[Any]]: ...


class NotchesCacheKey(NamedTuple):
    rect: QRectF
    total_frames: int


class NotchesCacheValue[T: (Time, Frame)](NamedTuple):
    scroll_rect: QRectF
    labels_notches: list[Notch[T]]
    rects_to_draw: list[tuple[QRectF, str]]


class NotchesCacheEntry[T: (Time, Frame)](NamedTuple):
    key: NotchesCacheKey
    value: NotchesCacheValue[T]


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        SettingsManager.signals.globalChanged.connect(self._on_settings_changed)

        self._mode: Literal["frame", "time"] = SettingsManager.global_settings.timeline.mode

        self.setAutoFillBackground(True)

        self.rect_f = QRectF()

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

        # Data needed for calculations
        self.total_frames = 1000
        self.total_time = Time(seconds=100)
        # Same default as BlankClip
        self.fps = Fraction(25)

        self.notches = dict[NotchProvider, list[Notch[Any]]]()

        # Optimization attributes
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)

        # Interaction state
        self.mousepressed = False
        self.hover_x: int | None = None
        self.is_events_blocked = False

        # Initialize cache
        self.notches_cache = self._init_notches_cache()

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
        self.rect_f = QRectF(event.rect())

        with QPainter(self) as painter:
            self._draw_widget(painter)

    def _draw_widget(self, painter: QPainter) -> None:
        setup_key = NotchesCacheKey(self.rect_f, self.total_frames)

        # Current cache entry for the current mode (Frame or Time)
        cache_entry = self.notches_cache[self.mode]

        # Unpack value components from the cache
        scroll_rect, labels_notches, rects_to_draw = cache_entry.value

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
            scroll_rect = QRectF(
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
            self.notches_cache[self.mode] = NotchesCacheEntry(
                setup_key,
                NotchesCacheValue(scroll_rect, labels_notches, rects_to_draw),
            )

        # Define the cursor line position
        cursor_line = QLineF(
            self.cursor_x, scroll_rect.top(), self.cursor_x, scroll_rect.top() + scroll_rect.height() - 1
        )

        # TODO: Normalize lines for external notch providers
        for provider, provider_notches in self.notches.items():
            if not provider.is_notches_visible:
                continue
            # provider_notches.norm_lines(self, scroll_rect)

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
        painter.fillRect(scroll_rect, self.palette().color(self.SCROLL_BAR_COLOR))

        # TODO: Draw custom notches from providers (e.g. bookmarks, keyframes)
        for provider, provider_notches in self.notches.items():
            if not provider.is_notches_visible:
                continue

            for p_notch in provider_notches:
                painter.setPen(p_notch.color)
                painter.drawLine(p_notch.line)

        # Draw current frame cursor
        painter.setPen(self.palette().color(self.BACKGROUND_COLOR))
        painter.drawLine(cursor_line)

        # Draw hover indicator if mouse is over the widget
        if self.hover_x is not None:
            if self.mode == "frame":
                text = str(self.x_to_frame(self.hover_x))
            else:
                text = self.x_to_time(self.hover_x).to_ts("{H:02d}:{M:02d}:{S:02d}.{ms:03d}")

            painter.setPen(QPen(self.palette().color(self.TEXT_COLOR), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QLineF(self.hover_x, self.rect_f.top(), self.hover_x, self.rect_f.bottom()))

            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(text)
            text_height = fm.height()

            rect_x = self.hover_x - (text_width / 2) - 3
            if rect_x < 0:
                rect_x = 0
            elif rect_x + text_width + 6 > self.rect_f.width():
                rect_x = self.rect_f.width() - text_width - 6

            rect_y = self.rect_f.top()

            bg_rect = QRectF(rect_x, rect_y, text_width + 6, text_height)
            painter.fillRect(bg_rect, self.palette().color(self.BACKGROUND_COLOR))
            painter.setPen(self.palette().color(self.TEXT_COLOR))
            painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, text)

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hover_x = None
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

        self.hover_x = int(max(0, min(event.position().x(), self.rect_f.width())))
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
            new_x = int(clamp(int(pos.x()), 0, self.rect_f.width()))

            self._cursor_val = new_x
            self.update()

            self.clicked.emit(self.x_to_frame(new_x), self.x_to_time(new_x))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def set_data(self, total_frames: int, fps: Fraction) -> None:
        self.total_frames = total_frames
        self.fps = fps

        if self.fps.numerator > 0:
            self.total_time = Time(seconds=total_frames * fps.denominator / fps.numerator)
        else:
            # FIXME: VFR update
            self.total_time = Time(seconds=0)

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
            return Time(0)
        return Time(seconds=(x * self.total_time.total_seconds() / self.rect_f.width()))

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
                return floor(cursor.total_seconds() / self.total_time.total_seconds() * width)

            if isinstance(cursor, Frame):
                return floor(cursor / self.total_frames * width)

            return cursor

        except (ZeroDivisionError, ValueError):
            return 0

    @contextmanager
    def block_events(self) -> Iterator[None]:
        self.is_events_blocked = True
        self.mousepressed = False
        try:
            yield
        finally:
            self.is_events_blocked = False

    def _init_notches_cache(self) -> dict[Literal["frame", "time"], NotchesCacheEntry[Any]]:
        return {
            "frame": NotchesCacheEntry(NotchesCacheKey(QRectF(), -1), NotchesCacheValue(QRectF(), [], [])),
            "time": NotchesCacheEntry(NotchesCacheKey(QRectF(), -1), NotchesCacheValue(QRectF(), [], [])),
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


@dataclass(slots=True, repr=False, eq=False, match_args=False)
class PlaybackSettings:
    seek_step: int = 1
    speed: float = 1.0
    uncapped: bool = False
    zone_frames: int = 100
    loop: bool = False


class PlaybackContainer(QWidget, IconReloadMixin):
    ICON_SIZE = QSize(24, 24)
    ICON_COLOR = QPalette.ColorRole.ToolTipText

    settingsChanged = Signal(int, float, bool)  # seek_step, speed, uncapped
    playZone = Signal(int, bool)  # zone_frames, loop
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
        self.zone_frame_spinbox.frameChanged.connect(self._on_zone_frames_changed)

        # Row with both edits
        zone_edits_row = QWidget(zone_widget)
        zone_edits_layout = QHBoxLayout(zone_edits_row)
        zone_edits_layout.setContentsMargins(0, 0, 0, 0)
        zone_edits_layout.setSpacing(4)
        zone_edits_layout.addWidget(self.zone_time_edit)
        zone_edits_layout.addWidget(self.zone_frame_spinbox)

        # Loop checkbox
        self.loop_checkbox = QCheckBox("Loop")
        self.loop_checkbox.toggled.connect(self._on_loop_changed)

        # Play zone button
        self.play_zone_btn = self.make_tool_button(IconName.PLAY, "Play Zone", zone_widget)
        self.play_zone_btn.clicked.connect(self._on_play_zone_clicked)

        # Row with loop and play button
        zone_controls_row = QWidget(zone_widget)
        zone_controls_layout = QHBoxLayout(zone_controls_row)
        zone_controls_layout.setContentsMargins(0, 0, 0, 0)
        zone_controls_layout.setSpacing(4)
        zone_controls_layout.addStretch()
        zone_controls_layout.addWidget(self.play_zone_btn)
        zone_controls_layout.addStretch()
        zone_controls_layout.addWidget(self.loop_checkbox)

        zone_layout.addRow("Zone Time/Frame", zone_edits_row)
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
    def fps(self) -> Fraction:
        return getattr(self, "_fps", Fraction(0))

    @fps.setter
    def fps(self, value: Fraction | float) -> None:
        self._fps = Fraction(value)

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

        # Sync zone settings
        with QSignalBlocker(self.zone_frame_spinbox):
            self.zone_frame_spinbox.setValue(self.settings.zone_frames)

        # Sync zone time based on FPS
        if self.fps.numerator > 0:
            with QSignalBlocker(self.zone_time_edit):
                self.zone_time_edit.setTime(
                    Time(seconds=self.settings.zone_frames * self.fps.denominator / self.fps.numerator).to_qtime()
                )

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
                    f"{a.vs_index}: {a.vs_name if a.vs_name else f'Audio {a.vs_index}'} ({a.chanels_layout.pretty_name})"  # noqa: E501
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

        # Convert frames to time based on FPS
        if self.fps.numerator > 0:
            with QSignalBlocker(self.zone_time_edit):
                self.zone_time_edit.setTime(
                    Time(seconds=new_frame * self.fps.denominator / self.fps.numerator).to_qtime()
                )

    def _on_zone_time_changed(self, new_time: QTime, old_time: QTime) -> None:
        # Convert time to frames based on FPS
        if self.fps.numerator > 0:
            seconds = new_time.msecsSinceStartOfDay() / 1000.0
            frames = max(1, round(seconds * self.fps.denominator / self.fps.numerator))

            self.settings.zone_frames = frames

            with QSignalBlocker(self.zone_frame_spinbox):
                self.zone_frame_spinbox.setValue(frames)

    def _on_loop_changed(self, checked: bool) -> None:
        self.settings.loop = checked

    def _on_play_zone_clicked(self) -> None:
        self.playZone.emit(self.settings.zone_frames, self.settings.loop)
        self.context_menu.close()

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
        loop.from_thread(self.setEnabled, False).result()

        try:
            with self.timeline.block_events():
                yield
        except BaseException:
            # Keep toolbar disabled on error
            raise
        else:
            loop.from_thread(self.setEnabled, True).result()
