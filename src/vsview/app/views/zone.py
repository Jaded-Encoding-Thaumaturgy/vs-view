from bisect import bisect_right
from collections.abc import Sequence
from datetime import timedelta

from PySide6.QtCore import QSignalBlocker, Qt, QTime, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QToolButton, QWidget

from ...assets import IconName
from ...types import Frame, Time
from ..icon import IconReloadMixin
from ..settings.models import ZoneSettings
from .components import FrameEdit, StepSpinBox, TimeEdit


class ZoneEditor(QWidget, IconReloadMixin):
    playRequested = Signal()
    zoneToCacheRequested = Signal()

    def __init__(self, settings: ZoneSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.settings = settings

        self._current = 0
        self._total = 0
        self._cum_durations: Sequence[Time] | None = None
        self._cache_range: range | None = None

        self.follow_checkbox = QCheckBox(self)
        self.follow_checkbox.setToolTip("Play Length frames from the current frame (backwards with a negative step)")
        self.follow_checkbox.toggled.connect(self._on_follow_toggled)

        self.start_time_edit, self.start_frame_edit = TimeEdit(self), FrameEdit(self)
        self.start_time_edit.valueChanged.connect(self._on_start_time_changed)
        self.start_frame_edit.frameChanged.connect(self._on_start_frame_changed)
        self.start_here_btn = self.make_tool_button(IconName.CROSSHAIR, "Set Start to the current frame", self)
        self.start_here_btn.clicked.connect(self._on_start_here_clicked)

        self.end_time_edit, self.end_frame_edit = TimeEdit(self), FrameEdit(self)
        self.end_time_edit.valueChanged.connect(self._on_end_time_changed)
        self.end_frame_edit.frameChanged.connect(self._on_end_frame_changed)
        self.end_here_btn = self.make_tool_button(IconName.CROSSHAIR, "Set End to the current frame", self)
        self.end_here_btn.clicked.connect(self._on_end_here_clicked)

        self.length_time_edit, self.length_frame_edit = TimeEdit(self), FrameEdit(self)
        self.length_frame_edit.setMinimum(1)
        self.length_time_edit.valueChanged.connect(self._on_length_time_changed)
        self.length_frame_edit.frameChanged.connect(self._on_length_frame_changed)

        self.step_spinbox = StepSpinBox(self, minimum=-1_000_000, maximum=1_000_000, value=1)
        self.step_spinbox.setMaximumWidth(self.length_time_edit.width())
        self.step_spinbox.setToolTip("Step size (negative values play backwards)")
        self.step_spinbox.valueChanged.connect(self._on_step_changed)

        self.from_cache_btn = self._make_text_button("From cache", "Use the render cache range as the zone")
        self.from_cache_btn.clicked.connect(self._on_from_cache_clicked)
        self.to_cache_btn = self._make_text_button("To cache", "Set the render cache range to the zone")
        self.to_cache_btn.clicked.connect(self.zoneToCacheRequested)

        self.loop_checkbox = QCheckBox("Loop", self)
        self.loop_checkbox.toggled.connect(self._on_loop_toggled)

        self.play_btn = self.make_tool_button(IconName.PLAY, "Play zone", self)
        self.play_btn.clicked.connect(self.playRequested)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(4)
        actions_layout.addWidget(self.from_cache_btn)
        actions_layout.addWidget(self.to_cache_btn)
        actions_layout.addStretch()
        actions_layout.addWidget(self.loop_checkbox)
        actions_layout.addWidget(self.play_btn)

        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Current", self), 0, 0)
        layout.addWidget(self.follow_checkbox, 0, 1)
        layout.addWidget(QLabel("Start", self), 1, 0)
        layout.addLayout(self._make_row(self.start_time_edit, self.start_frame_edit), 1, 1)
        layout.addWidget(self.start_here_btn, 1, 2)
        layout.addWidget(QLabel("End", self), 2, 0)
        layout.addLayout(self._make_row(self.end_time_edit, self.end_frame_edit), 2, 1)
        layout.addWidget(self.end_here_btn, 2, 2)
        layout.addWidget(QLabel("Length", self), 3, 0)
        layout.addLayout(self._make_row(self.length_time_edit, self.length_frame_edit), 3, 1)
        layout.addWidget(QLabel("Play Step", self), 4, 0)
        layout.addWidget(self.step_spinbox, 4, 1, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(separator, 5, 0, 1, 3)
        layout.addLayout(actions_layout, 6, 0, 1, 3)
        layout.setColumnStretch(1, 1)

        self.refresh()

    def set_data(self, total_frames: int, cum_durations: Sequence[Time] | None) -> None:
        self._total = total_frames
        self._cum_durations = cum_durations

        total_time = cum_durations[-1] if cum_durations else Time()
        last = Frame(max(0, total_frames - 1))

        for frame_edit, maximum in [
            (self.start_frame_edit, last),
            (self.end_frame_edit, last),
            (self.length_frame_edit, Frame(max(1, total_frames))),
        ]:
            with QSignalBlocker(frame_edit):
                frame_edit.setMaximum(maximum)

        for time_edit in (self.start_time_edit, self.end_time_edit, self.length_time_edit):
            with QSignalBlocker(time_edit):
                time_edit.setMaximumTime(total_time.to_qtime())

        self.refresh()

    def set_cache_state(self, frames: range | None, unavailable_reason: str | None) -> None:
        self._cache_range = frames
        self.from_cache_btn.setEnabled(frames is not None)
        self.to_cache_btn.setEnabled(unavailable_reason is None)
        self.to_cache_btn.setToolTip(unavailable_reason or "Set the render cache range to the zone")

    def set_start(self, frame: int) -> None:
        self.settings.set_start(frame, self._current, self._total)
        self.refresh()

    def set_end(self, frame: int) -> None:
        self.settings.set_end(frame, self._current, self._total)
        self.refresh()

    def refresh(self, current: int | None = None) -> None:
        if current is not None:
            self._current = current

        s = self.settings
        start, end = s.bounds(self._current, self._total)
        length = s.length if s.follow else end - start + 1

        for frame_edit, frame in [
            (self.start_frame_edit, start),
            (self.end_frame_edit, end),
            (self.length_frame_edit, length),
        ]:
            with QSignalBlocker(frame_edit):
                frame_edit.setValue(frame)

        if self._cum_durations:
            for time_edit, time in [
                (self.start_time_edit, self._frame_to_time(start)),
                (self.end_time_edit, self._frame_to_time(end)),
                (self.length_time_edit, self._frame_to_time(start + length) - self._frame_to_time(start)),
            ]:
                with QSignalBlocker(time_edit):
                    time_edit.setTime(Time(seconds=time.total_seconds()).to_qtime())

        for widget in (
            self.start_time_edit,
            self.start_frame_edit,
            self.start_here_btn,
            self.end_time_edit,
            self.end_frame_edit,
            self.end_here_btn,
        ):
            widget.setEnabled(not s.follow)

        with QSignalBlocker(self.follow_checkbox):
            self.follow_checkbox.setChecked(s.follow)

        with QSignalBlocker(self.step_spinbox):
            self.step_spinbox.setValue(s.step)

        with QSignalBlocker(self.loop_checkbox):
            self.loop_checkbox.setChecked(s.loop)

    def _make_row(self, time_edit: TimeEdit, frame_edit: FrameEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(time_edit)
        row.addWidget(frame_edit)
        return row

    def _make_text_button(self, text: str, tooltip: str) -> QToolButton:
        btn = self.make_tool_button(IconName.DATABASE, tooltip, self)
        btn.setText(text)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setEnabled(False)
        return btn

    def _frame_to_time(self, frame: int) -> timedelta:
        # The time of a frame is the time it starts at, as in the main time edit.
        if not self._cum_durations or frame <= 0:
            return Time()

        return self._cum_durations[min(frame, len(self._cum_durations)) - 1]

    def _time_to_frame(self, time: QTime) -> int | None:
        if not self._cum_durations:
            return None

        return min(bisect_right(self._cum_durations, Time.from_qtime(time)), max(0, self._total - 1))

    @Slot(object, object)
    def _on_start_frame_changed(self, new_frame: int, old_frame: int) -> None:
        self.set_start(new_frame)

    @Slot(QTime, QTime)
    def _on_start_time_changed(self, new_time: QTime, old_time: QTime) -> None:
        if (frame := self._time_to_frame(new_time)) is not None:
            self.set_start(frame)

    @Slot()
    def _on_start_here_clicked(self) -> None:
        self.set_start(self._current)

    @Slot(object, object)
    def _on_end_frame_changed(self, new_frame: int, old_frame: int) -> None:
        self.set_end(new_frame)

    @Slot(QTime, QTime)
    def _on_end_time_changed(self, new_time: QTime, old_time: QTime) -> None:
        if (frame := self._time_to_frame(new_time)) is not None:
            self.set_end(frame)

    @Slot()
    def _on_end_here_clicked(self) -> None:
        self.set_end(self._current)

    @Slot(object, object)
    def _on_length_frame_changed(self, new_frame: int, old_frame: int) -> None:
        self.settings.set_length(new_frame, self._total)
        self.refresh()

    @Slot(QTime, QTime)
    def _on_length_time_changed(self, new_time: QTime, old_time: QTime) -> None:
        if not self._cum_durations:
            return

        # The duration is measured from the zone start, so that it stays exact with VFR.
        start, _ = self.settings.bounds(self._current, self._total)
        end_time = self._frame_to_time(start) + Time.from_qtime(new_time)
        self.settings.set_length(max(1, bisect_right(self._cum_durations, end_time) - start), self._total)
        self.refresh()

    @Slot(bool)
    def _on_follow_toggled(self, checked: bool) -> None:
        self.settings.set_follow(checked, self._current, self._total)
        self.refresh()

    @Slot(int)
    def _on_step_changed(self, value: int) -> None:
        if value == 0:
            value = 1 if self.settings.step < 0 else -1

        self.settings.step = value
        self.refresh()

    @Slot(bool)
    def _on_loop_toggled(self, checked: bool) -> None:
        self.settings.loop = checked

    @Slot()
    def _on_from_cache_clicked(self) -> None:
        if self._cache_range:
            self.settings.set_range(self._cache_range.start, self._cache_range[-1])
            self.refresh()
