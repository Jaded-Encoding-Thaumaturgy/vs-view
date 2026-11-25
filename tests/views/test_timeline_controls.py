from __future__ import annotations

import gc
import weakref
from typing import Any, Literal, NamedTuple

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QRectF, Qt, QTime
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFocusEvent,
    QFontMetrics,
    QHideEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QValidator,
)
from PySide6.QtWidgets import QApplication, QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from vsview.app.outputs.audio import PrettyChannelsLayout
from vsview.app.settings import SettingsManager
from vsview.app.views.timeline import (
    CustomNotch,
    FrameEdit,
    HoverLabel,
    Notch,
    PlaybackContainer,
    StepSpinBox,
    Timeline,
    TimelineControlBar,
    TimelineHoverPopup,
    generate_label_format,
)
from vsview.types import Frame, Time
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class DummyAudioOutput(NamedTuple):
    vs_index: int
    vs_name: str
    channels_layout: PrettyChannelsLayout


@pytest.fixture
def timeline_control_bar(qtbot: QtBot) -> TimelineControlBar:
    """Provide a TimelineControlBar with 100 frames initialized."""
    bar = TimelineControlBar()
    qtbot.addWidget(bar)
    bar.resize(1000, 33)
    bar.set_data(100)
    return bar


# Notch & CustomNotch Tests
def test_custom_notch_equality_and_hashing() -> None:
    c1 = CustomNotch("id_a", Frame(10))
    c2 = CustomNotch("id_a", Frame(99))  # Same id, different frame
    c3 = CustomNotch("id_b", Frame(10))  # Different id, same frame

    assert c1 == c2
    assert c1 != c3
    assert hash(c1) == hash(c2) == hash("id_a")
    assert hash(c1) != hash(c3)

    assert (c1 == "non_custom_notch") is False

    s = {c1, c2, c3}
    assert len(s) == 2
    assert c1 in s
    assert c3 in s


# TimelineHoverPopup Tests
def test_hover_popup_update_state(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    popup = timeline.hover_popup

    SettingsManager.global_settings.timeline.hover_zoom_factor = 7.5
    SettingsManager.global_settings.timeline.hover_zoom_radius = 80

    popup.update_state(250)

    assert popup.hover_x == 250
    assert popup.zoom_factor == 7.5
    assert popup.radius == 80
    assert popup.width() == 160  # radius * 2


def test_hover_popup_transient_parent(timeline_control_bar: TimelineControlBar) -> None:
    timeline_control_bar.show()

    popup = timeline_control_bar.timeline.hover_popup
    popup.update_state(250)
    popup.show()

    assert popup.windowHandle() is not None
    assert popup.windowHandle().transientParent() is not None
    assert popup.windowHandle().transientParent() == timeline_control_bar.window().windowHandle()


def test_hover_popup_notch_label_and_value_display(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    # Zoom out so the whole bar fits in the popup and every label is on screen.
    SettingsManager.global_settings.timeline.hover_zoom_factor = 1.0
    SettingsManager.global_settings.timeline.hover_zoom_radius = 500
    popup = timeline.hover_popup
    popup.update_state(500)

    # Static label point notch
    timeline.add_notch("bookmarks", Frame(10), label="Cut Scene")
    # Template label point notch
    timeline.add_notch("bookmarks", Frame(25), label="Marker @ {}")
    # Range notch with label
    timeline.add_notch("dialogue", Frame(10), end_data=Frame(50), label="Dialogue")
    # Range notch without label
    timeline.add_notch("ranges", Frame(10), end_data=Frame(50), label="")

    spy = mocker.spy(popup, "_draw_label_pill")
    popup.paintEvent(QPaintEvent(popup.rect()))

    displayed_labels = [call.args[1].text for call in spy.call_args_list]
    assert "Cut Scene" in displayed_labels
    assert "Marker @ 25" in displayed_labels
    assert "[10, 50] Dialogue" in displayed_labels
    assert "[10, 50]" in displayed_labels

    # In time mode with custom timestamp
    timeline.mode = "time"
    t_val = Time(hours=1, minutes=23, seconds=45, milliseconds=678)
    timeline.add_notch("times", t_val, label="Timestamp @ {}")
    spy.reset_mock()
    popup.paintEvent(QPaintEvent(popup.rect()))

    time_labels = [call.args[1].text for call in spy.call_args_list]
    assert "Timestamp @ 01:23:45.678" in time_labels


def test_hover_popup_label_staggering(timeline_control_bar: TimelineControlBar) -> None:
    popup = timeline_control_bar.timeline.hover_popup
    fm = QFontMetrics(popup.font())

    assert popup.apply_staggering([], fm) == []

    # Two labels far apart: both offset 0
    labels = [
        HoverLabel(text="A", x=10.0, color=QColor("red")),
        HoverLabel(text="B", x=300.0, color=QColor("blue")),
    ]
    staggered = popup.apply_staggering(labels, fm)
    assert len(staggered) == 2
    assert staggered[0].y_offset == 0
    assert staggered[1].y_offset == 0

    # 4 closely spaced labels: stagger across 3 offsets [0, -20, -40, 0]
    crowded = [
        HoverLabel(text="A", x=10.0, color=QColor("red")),
        HoverLabel(text="B", x=12.0, color=QColor("blue")),
        HoverLabel(text="C", x=14.0, color=QColor("green")),
        HoverLabel(text="D", x=16.0, color=QColor("yellow")),
    ]
    staggered_crowded = popup.apply_staggering(crowded, fm)
    offsets = [lbl.y_offset for lbl in staggered_crowded]
    assert offsets == [0, -20, -40, 0]


# Timeline widget (Scrubber Slider) Tests
def test_timeline_cursor_x_protected_during_mousepress(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    timeline.cursor_x = Frame(20)
    assert timeline.x_to_frame(timeline.cursor_x) == Frame(20)

    timeline.mousepressed = True
    timeline.cursor_x = Frame(80)  # Should be ignored during active scrub
    assert timeline.x_to_frame(timeline.cursor_x) == Frame(20)


def test_timeline_mouse_press_and_click_emission(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    # Paint once to set up notches_cache
    image = QImage(1000, 33, QImage.Format.Format_ARGB32_Premultiplied)
    with QPainter(image) as painter:
        timeline.draw_widget(painter)

    emitted: list[tuple[Frame, Time]] = []
    timeline.clicked.connect(lambda f, t: emitted.append((f, t)))

    scroll_rect = timeline.notches_cache[timeline.mode].value.scroll_rect
    center_y = scroll_rect.center().y()

    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(500, center_y),
        QPointF(500, center_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mousePressEvent(press_event)

    assert timeline.mousepressed is True
    assert len(emitted) == 1
    assert emitted[0][0] == Frame(50)

    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(500, center_y),
        QPointF(500, center_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mouseReleaseEvent(release_event)
    assert timeline.mousepressed is False


def test_timeline_mouse_drag_vertical_deviation_continues_seeking(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    emitted: list[tuple[Frame, Time]] = []
    timeline.clicked.connect(lambda f, t: emitted.append((f, t)))

    # 1. Click at (200, 5) - top region of the timeline
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(200, 5),
        QPointF(200, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mousePressEvent(press_event)
    assert timeline.mousepressed is True
    assert len(emitted) == 1
    assert emitted[-1][0] == Frame(20)

    # 2. Drag high above the timeline (y = -150)
    drag_up = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(400, -150),
        QPointF(400, -150),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mouseMoveEvent(drag_up)
    assert len(emitted) == 2
    assert emitted[-1][0] == Frame(40)

    # 3. Drag deep below the timeline (y = 200)
    drag_down = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(600, 200),
        QPointF(600, 200),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mouseMoveEvent(drag_down)
    assert len(emitted) == 3
    assert emitted[-1][0] == Frame(60)

    # 4. Release mouse outside the widget: hover state is cleaned up
    release_outside = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(600, 200),
        QPointF(600, 200),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mouseReleaseEvent(release_outside)
    assert timeline.mousepressed is False
    assert timeline.hover_x is None


def test_timeline_mouse_right_click_ignored_by_scrubber(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    emitted: list[tuple[Frame, Time]] = []
    timeline.clicked.connect(lambda f, t: emitted.append((f, t)))

    right_click = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(300, 15),
        QPointF(300, 15),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mousePressEvent(right_click)
    assert timeline.mousepressed is False
    assert len(emitted) == 0


def _mouse_event(
    event_type: QEvent.Type,
    x: float,
    *,
    pressed: bool = False,
    shift: bool = False,
) -> QMouseEvent:
    button = Qt.MouseButton.LeftButton if event_type == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    buttons = Qt.MouseButton.LeftButton if pressed else Qt.MouseButton.NoButton
    modifiers = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    return QMouseEvent(event_type, QPointF(x, 15), QPointF(x, 15), button, buttons, modifiers)


def _fine_seek_timeline(qtbot: QtBot, total_frames: int) -> tuple[Timeline, list[tuple[Frame, Time]]]:
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(1000, 33)
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    timeline.set_data(total_frames, [Time(seconds=(i + 1) / 25) for i in range(total_frames)])

    emitted: list[tuple[Frame, Time]] = []
    timeline.clicked.connect(lambda f, t: emitted.append((f, t)))
    return timeline, emitted


@pytest.mark.parametrize("distance", [1, 2, 5])
def test_timeline_shift_drag_seeks_one_frame_per_fine_seek_distance(qtbot: QtBot, distance: int) -> None:
    SettingsManager.global_settings.timeline.fine_seek_distance = distance
    timeline, emitted = _fine_seek_timeline(qtbot, 10_000)

    timeline.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 500, pressed=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500, pressed=True, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500 + 3 * distance, pressed=True, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500 - 2 * distance, pressed=True, shift=True))

    assert [frame for frame, _ in emitted] == [Frame(5000), Frame(5000), Frame(5003), Frame(4998)]
    assert timeline.hover_frame == Frame(4998)


def test_timeline_releasing_shift_returns_to_the_mouse_position(qtbot: QtBot) -> None:
    timeline, emitted = _fine_seek_timeline(qtbot, 10_000)

    timeline.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 500, pressed=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500, pressed=True, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 600, pressed=True, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 601, pressed=True))

    assert [frame for frame, _ in emitted] == [Frame(5000), Frame(5000), Frame(5050), Frame(6010)]
    assert timeline.hover_x == 601


def test_timeline_shift_hover_then_click_seeks_the_fine_frame(qtbot: QtBot) -> None:
    timeline, emitted = _fine_seek_timeline(qtbot, 10_000)

    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 500, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 514, shift=True))
    assert emitted == []

    timeline.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 514, pressed=True, shift=True))

    assert emitted == [(Frame(5007), Time(seconds=5007 / 25))]
    assert timeline.cursor_x == timeline.cursor_to_x(Frame(5007))


def test_timeline_shift_is_ignored_when_frames_are_wider_than_the_fine_distance(qtbot: QtBot) -> None:
    timeline, emitted = _fine_seek_timeline(qtbot, 100)

    timeline.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, 500, pressed=True, shift=True))
    timeline.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, 520, pressed=True, shift=True))

    assert [frame for frame, _ in emitted] == [Frame(50), Frame(52)]


def test_timeline_notch_management(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline

    assert "bookmarks" not in timeline.custom_notches or len(timeline.custom_notches["bookmarks"]) == 0

    timeline.add_notch("bookmarks", Frame(10), label="Intro", color=Qt.GlobalColor.blue)
    assert any(n.data == Frame(10) and n.label == "Intro" for n in timeline.custom_notches["bookmarks"])
    assert not any(n.data == Frame(11) for n in timeline.custom_notches["bookmarks"])

    timeline.add_notch("bookmarks", Frame(50), label="Climax")
    assert {int(n.data) for n in timeline.custom_notches["bookmarks"]} == {10, 50}

    timeline.discard_notch("bookmarks", Frame(10))
    assert {int(n.data) for n in timeline.custom_notches["bookmarks"]} == {50}


def test_timeline_range_and_custom_notches(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline

    # Adding a range notch with end_data
    timeline.add_notch("chapters", Frame(10), end_data=Frame(30), label="Scene 1", color=Qt.GlobalColor.green)
    chapter_notches = [n for n in timeline.custom_notches["chapters"] if n.data == Frame(10)]
    assert len(chapter_notches) == 1
    assert chapter_notches[0].end_data == Frame(30)
    assert chapter_notches[0].label == "Scene 1"

    # Discarding the range notch
    timeline.discard_notch("chapters", Frame(10), end_data=Frame(30))
    assert len(timeline.custom_notches["chapters"]) == 0


def test_timeline_display_scale_follows_the_global_setting(
    timeline_control_bar: TimelineControlBar, qt_event_loop: QtEventLoop
) -> None:
    timeline = timeline_control_bar.timeline

    SettingsManager.global_settings.timeline.display_scale = 1.75
    SettingsManager.signals.globalChanged.emit()

    assert timeline.display_scale == 1.75


@pytest.mark.parametrize(
    ("open_mode", "global_mode"),
    [("time", "frame"), ("frame", "time")],
)
def test_timeline_global_save_keeps_the_open_mode(
    timeline_control_bar: TimelineControlBar,
    qt_event_loop: QtEventLoop,
    open_mode: Literal["frame", "time"],
    global_mode: Literal["frame", "time"],
) -> None:
    timeline = timeline_control_bar.timeline
    timeline.mode = open_mode

    SettingsManager.global_settings.timeline.mode = global_mode
    SettingsManager.signals.globalChanged.emit()

    assert timeline.mode == open_mode
    assert timeline.mode_selector.index == timeline.MODES.index(open_mode)


def test_timeline_block_events_and_reset_interaction(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.mousepressed = True
    timeline.hover_x = 100

    timeline.reset_interaction()
    assert timeline.mousepressed is False
    assert timeline.hover_x is None

    timeline.reset_interaction()
    timeline.is_events_blocked = True
    try:
        # Mouse event during block is ignored
        dummy_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(50, 10),
            QPointF(50, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        timeline.mousePressEvent(dummy_event)
        assert timeline.mousepressed is False
    finally:
        timeline.is_events_blocked = False


def _time_labels(timeline: Timeline) -> list[str]:
    return [text for _, text in timeline.notches_cache["time"].value.rects_to_draw]


def _time_mode_timeline(qtbot: QtBot, fps: int) -> Timeline:
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(1000, 40)
    timeline.mode = "time"
    timeline.set_data(1000, [Time(seconds=i / fps) for i in range(1, 1001)])
    return timeline


def test_timeline_set_data_refreshes_time_labels_when_only_durations_change(qtbot: QtBot) -> None:
    timeline = _time_mode_timeline(qtbot, 24)
    timeline.grab()
    labels_24fps = _time_labels(timeline)
    reference = _time_mode_timeline(qtbot, 60)
    reference.grab()

    timeline.set_data(1000, [Time(seconds=i / 60) for i in range(1, 1001)])
    timeline.grab()

    assert _time_labels(timeline) == _time_labels(reference)
    assert _time_labels(timeline) != labels_24fps


def test_timeline_set_data_moves_time_notches_when_only_durations_change(qtbot: QtBot) -> None:
    timeline = _time_mode_timeline(qtbot, 24)
    timeline.add_notch("bookmarks", Time(seconds=10), label="Ten seconds")
    timeline.grab()
    (notch,) = timeline.custom_notches["bookmarks"]
    assert notch.line.x1() == 240

    timeline.set_data(1000, [Time(seconds=i / 60) for i in range(1, 1001)])
    timeline.grab()

    assert notch.line.x1() == 600


@pytest.mark.parametrize("label", ["{foo}", "unbalanced {", "{1}", "{} and {}", "{0.foo}", "{0[foo]}"])
def test_hover_popup_shows_raw_label_when_format_fails(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture, label: str
) -> None:
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    popup = timeline.hover_popup
    timeline.add_notch("bookmarks", Frame(50), label=label)
    popup.update_state(500)
    spy = mocker.spy(popup, "_draw_label_pill")

    popup.grab()

    assert [call.args[1].text for call in spy.call_args_list] == [label]


def test_timeline_delete_later_deletes_timeline_and_hover_popup(qtbot: QtBot) -> None:
    # Not registered with qtbot: both widgets are deleted inside the test.
    timeline = Timeline()
    popup = timeline.hover_popup

    timeline.deleteLater()
    qtbot.waitUntil(lambda: not Shiboken.isValid(timeline) and not Shiboken.isValid(popup), timeout=1000)

    assert Shiboken.isValid(timeline) is False
    assert Shiboken.isValid(popup) is False


def test_hover_popup_is_deleted_with_the_timeline_parent(qtbot: QtBot) -> None:
    # Not registered with qtbot: the parent is deleted inside the test.
    def _create_and_delete() -> tuple[weakref.ref[Timeline], weakref.ref[Any]]:
        parent = QWidget()
        timeline = Timeline(parent)
        timeline.hover_popup.update_state(5)
        popup = timeline.hover_popup

        tl_ref = weakref.ref(timeline)
        popup_ref = weakref.ref(popup)

        parent.deleteLater()
        qtbot.waitUntil(lambda: not Shiboken.isValid(parent) and not Shiboken.isValid(popup), timeout=1000)

        assert Shiboken.isValid(timeline) is False
        assert Shiboken.isValid(popup) is False
        return tl_ref, popup_ref

    tl_ref, popup_ref = _create_and_delete()
    gc.collect()

    assert tl_ref() is None
    assert popup_ref() is None


def test_timeline_and_popup_handle_partial_deletion_safely(qtbot: QtBot, qt_event_loop: QtEventLoop) -> None:
    bar = TimelineControlBar()
    qtbot.addWidget(bar)
    bar.set_data(100)
    bar.timeline.hover_popup.deleteLater()
    qtbot.waitUntil(lambda: not Shiboken.isValid(bar.timeline.hover_popup), timeout=1000)

    bar.disable()
    bar.timeline.hide()
    bar.timeline.reset_interaction()

    # Guarded no-op when a standalone popup outlives a parent-deleted Timeline.
    parent = QWidget()
    orphan_tl = Timeline(parent)
    orphan_popup = orphan_tl.hover_popup
    qtbot.addWidget(orphan_popup)
    orphan_popup.update_state(5)
    orphan_tl.destroyed.disconnect(orphan_popup.deleteLater)
    parent.deleteLater()
    qtbot.waitUntil(lambda: not Shiboken.isValid(orphan_tl), timeout=1000)

    assert Shiboken.isValid(orphan_popup) is True
    orphan_popup.update_state(10)
    orphan_popup.paintEvent(QPaintEvent(orphan_popup.rect()))


def _add_spread_notches(timeline: Timeline, count: int, step_frames: int) -> None:
    timeline.set_data(100_000)
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    for i in range(count):
        timeline.add_notch("keyframes", Frame(i * step_frames), label=f"k{i}" + "x" * (i % 7))


def _set_hover_zoom(zoom: float, radius: int) -> None:
    SettingsManager.global_settings.timeline.hover_zoom_factor = zoom
    SettingsManager.global_settings.timeline.hover_zoom_radius = radius


def test_hover_popup_draws_only_notches_in_the_zoomed_range(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    timeline = timeline_control_bar.timeline
    _add_spread_notches(timeline, 2000, 50)
    _set_hover_zoom(4.0, 50)
    popup = timeline.hover_popup
    popup.update_state(500)
    draw_spy = mocker.spy(Notch, "draw")

    popup.grab()

    drawn = sorted(call.args[0].label for call in draw_spy.call_args_list)
    visible = sorted(n.label for n in timeline.custom_notches["keyframes"] if 488 <= n.line.x1() <= 512)
    assert len(visible) == 51
    assert drawn == visible


@pytest.mark.parametrize(
    ("count", "step_frames", "zoom", "hover_x"),
    [
        (2000, 50, 4.0, 500),
        (2000, 50, 1.0, 300),
        (2000, 50, 20.0, 990),
        (300, 300, 20.0, 450),
        (300, 300, 20.0, 880),
    ],
)
def test_hover_popup_visible_labels_match_full_staggering(
    timeline_control_bar: TimelineControlBar,
    mocker: MockerFixture,
    count: int,
    step_frames: int,
    zoom: float,
    hover_x: int,
) -> None:
    timeline = timeline_control_bar.timeline
    _add_spread_notches(timeline, count, step_frames)
    _set_hover_zoom(zoom, 50)
    popup = timeline.hover_popup
    popup.update_state(hover_x)
    fm = QFontMetrics(popup.font())
    padding = TimelineHoverPopup.LABEL_PADDING_H

    def visible(lbl: HoverLabel) -> bool:
        half_width = fm.horizontalAdvance(lbl.text) / 2 + padding
        return lbl.x + half_width > 0 and lbl.x - half_width < popup.width()

    def key(lbl: HoverLabel) -> tuple[str, float, float]:
        return lbl.text, round(lbl.x, 6), lbl.y_offset

    every_label = sorted(
        (
            HoverLabel(n.label, popup.radius + zoom * (n.line.x1() - hover_x), n.color)
            for n in timeline.custom_notches["keyframes"]
        ),
        key=lambda lbl: lbl.x,
    )
    expected = [key(lbl) for lbl in popup.apply_staggering(every_label, fm) if visible(lbl)]
    pill_spy = mocker.spy(popup, "_draw_label_pill")

    popup.grab()

    drawn = [key(call.args[1]) for call in pill_spy.call_args_list if visible(call.args[1])]
    assert len(expected) > 0
    assert drawn == expected


@pytest.mark.parametrize("x", [-9_990.0, -10_000.0, -50_000.0, 0.0, 500.0])
def test_hover_popup_first_label_starts_at_the_lowest_level(timeline_control_bar: TimelineControlBar, x: float) -> None:
    popup = timeline_control_bar.timeline.hover_popup
    fm = QFontMetrics(popup.font())
    labels = [HoverLabel("A", x, QColor("red")), HoverLabel("B", x + 2.0, QColor("blue"))]

    staggered = popup.apply_staggering(labels, fm)

    assert [lbl.y_offset for lbl in staggered] == [0, -20]


def test_hover_popup_stagger_levels_do_not_depend_on_the_hover_position(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    timeline = timeline_control_bar.timeline
    timeline.set_data(1000)
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    # One notch per pixel: at zoom 20 every label is close to the previous one, so all labels form one run from x=0.
    for i in range(1000):
        timeline.add_notch("keyframes", Frame(i), label=f"n{i:04d}")
    # The radius shows +-20 timeline pixels, so n0520 is on screen at both hover positions.
    _set_hover_zoom(20.0, 400)
    popup = timeline.hover_popup
    pill_spy = mocker.spy(popup, "_draw_label_pill")

    def level_of(text: str, hover_x: int) -> float:
        pill_spy.reset_mock()
        popup.update_state(hover_x)
        popup.grab()
        return next(call.args[1].y_offset for call in pill_spy.call_args_list if call.args[1].text == text)

    levels = {hover_x: level_of("n0520", hover_x) for hover_x in (505, 535)}

    assert levels == {505: -20, 535: -20}


def test_hover_popup_culling_follows_notch_changes(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    timeline = timeline_control_bar.timeline
    timeline.set_data(1000)
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    _set_hover_zoom(4.0, 50)
    popup = timeline.hover_popup
    popup.update_state(500)
    pill_spy = mocker.spy(popup, "_draw_label_pill")

    def paint_labels() -> list[str]:
        pill_spy.reset_mock()
        popup.grab()
        return [call.args[1].text for call in pill_spy.call_args_list]

    timeline.add_notch("bookmarks", Frame(500), label="first")
    assert paint_labels() == ["first"]

    timeline.discard_notch("bookmarks", Frame(500))
    timeline.add_notch("bookmarks", Frame(501), label="second")
    assert paint_labels() == ["second"]

    timeline.custom_notches.pop("bookmarks")
    assert paint_labels() == []

    timeline.add_notch("bookmarks", Frame(499), label="third")
    assert paint_labels() == ["third"]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda tl: tl.force_cursor(Frame(10)), id="cursor"),
        pytest.param(lambda tl: tl.set_cache_notches(10, 50, "Cache 5 / 41", [(10, 14)]), id="cache-notches"),
    ],
)
@pytest.mark.parametrize("popup_visible", [True, False])
def test_hover_popup_repaints_with_the_timeline(
    timeline_control_bar: TimelineControlBar,
    qtbot: QtBot,
    mocker: MockerFixture,
    change: Any,
    popup_visible: bool,
) -> None:
    timeline_control_bar.show()
    qtbot.waitExposed(timeline_control_bar)
    timeline = timeline_control_bar.timeline
    popup = timeline.hover_popup
    popup.update_state(500)
    popup.setVisible(popup_visible)
    qtbot.wait(0)
    paint_spy = mocker.spy(timeline, "draw_widget")
    popup_update_spy = mocker.spy(popup, "update")

    change(timeline)

    qtbot.waitUntil(lambda: paint_spy.called, timeout=1000)
    assert popup_update_spy.called is popup_visible


def test_timeline_skips_custom_notches_outside_the_bar(qtbot: QtBot, mocker: MockerFixture) -> None:
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(1000, 40)
    timeline.set_data(100)
    timeline.add_notch("bookmarks", Frame(50), label="inside")
    timeline.add_notch("bookmarks", Frame(150), label="beyond the end")
    timeline.add_notch("ranges", Frame(90), end_data=Frame(200), label="crossing the end")
    draw_spy = mocker.spy(Notch, "draw")

    timeline.grab()

    assert sorted(call.args[0].label for call in draw_spy.call_args_list) == ["crossing the end", "inside"]


# PlaybackContainer Tests
def test_playback_container_loop_and_play_zone(timeline_control_bar: TimelineControlBar, qtbot: QtBot) -> None:
    container = timeline_control_bar.playback_container
    timeline_control_bar.set_data(1000, [(i + 1) / 25.0 for i in range(1000)])

    zone_emissions: list[tuple[int, bool, int]] = []
    container.playZone.connect(lambda z, loop, step: zone_emissions.append((z, loop, step)))

    container.loop_checkbox.setChecked(True)
    assert container.settings.loop is True

    container.zone_frame_spinbox.setValue(350)
    assert container.settings.zone_frames == 350
    assert timeline_control_bar.cum_durations is not None
    assert len(timeline_control_bar.cum_durations) == 1000
    assert container.zone_time_edit.time() == timeline_control_bar.cum_durations[349].to_qtime()

    qtbot.mouseClick(container.play_zone_btn, Qt.MouseButton.LeftButton)
    assert len(zone_emissions) == 1
    assert zone_emissions[0] == (350, True, 1)


def test_playback_container_step_spinbox_skips_zero(qtbot: QtBot) -> None:
    parent = PlaybackContainer()
    qtbot.addWidget(parent)
    spinbox = StepSpinBox(parent, minimum=-100, maximum=100, value=1)
    qtbot.addWidget(spinbox)

    # Step down from 1 skips 0 to -1
    spinbox.stepBy(-1)
    assert spinbox.value() == -1

    # Step up from -1 skips 0 to 1
    spinbox.stepBy(1)
    assert spinbox.value() == 1

    # Normal step from 1 to 2
    spinbox.stepBy(1)
    assert spinbox.value() == 2

    # Step down from 2 to 1
    spinbox.stepBy(-1)
    assert spinbox.value() == 1


def test_playback_container_step_changed_handler(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    container.settings.step = 1
    # When zero is passed, resets to -1 if step > 0
    container.step_frame_spinbox.valueChanged.emit(0)
    assert container.settings.step == -1

    container.step_frame_spinbox.valueChanged.emit(0)
    assert container.settings.step == 1

    container.step_frame_spinbox.setValue(5)
    assert container.settings.step == 5


def test_playback_container_seek_step_settings(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    settings_emitted: list[tuple[int, float, bool]] = []
    container.settingsChanged.connect(lambda s, spd, u: settings_emitted.append((s, spd, u)))

    container.seek_step_spinbox.setValue(48)
    assert container.settings.seek_step == 48
    assert len(settings_emitted) == 1
    assert settings_emitted[0] == (48, 1.0, False)

    # Reset to global
    SettingsManager.global_settings.timeline.seek_step = 24
    container.reset_seek_step_to_global_action.trigger()
    assert container.settings.seek_step == 24
    assert container.seek_step_spinbox.value() == 24


@pytest.mark.parametrize(("slider", "speed"), [(0, 0.25), (17, 0.5), (50, 1.0), (75, 4.5), (100, 8.0)])
def test_playback_container_speed_slider_sets_the_speed(qtbot: QtBot, slider: int, speed: float) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    container.speed_slider.setValue(slider)

    assert container.settings.speed == speed


def test_playback_container_speed_reset_goes_back_to_normal_speed(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)
    container.speed_slider.setValue(100)

    qtbot.mouseClick(container.speed_reset_btn, Qt.MouseButton.LeftButton)

    assert container.settings.speed == 1.0
    assert container.speed_slider.value() == 50


def test_playback_container_uncap_fps_toggle(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    container.uncap_checkbox.setChecked(True)
    assert container.settings.uncapped is True
    assert container.speed_slider.isEnabled() is False
    assert container.speed_reset_btn.isEnabled() is False

    container.uncap_checkbox.setChecked(False)
    assert container.settings.uncapped is False
    assert container.speed_slider.isEnabled() is True
    assert container.speed_reset_btn.isEnabled() is True


def test_playback_container_volume_mute_controls(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    vol_signals: list[float] = []
    mute_signals: list[bool] = []
    container.volumeChanged.connect(vol_signals.append)
    container.muteChanged.connect(mute_signals.append)

    container.volume = 0.8
    assert container.volume == 0.8
    assert container.volume_slider.value() == 800

    container.is_muted = True
    assert container.is_muted is True
    assert container.volume == 0.0  # Muted volume is 0.0
    assert container.raw_volume == 0.8  # Preserves raw volume

    # Changing slider unmutes
    container.volume_slider.setValue(600)
    assert container.is_muted is False
    assert pytest.approx(container.volume) == 0.6
    assert mute_signals[-1] is False


def test_playback_container_audio_delay_settings(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    delay_signals: list[float] = []
    container.audioDelayChanged.connect(delay_signals.append)

    container.audio_delay = 0.125
    assert container.audio_delay == 0.125
    assert pytest.approx(container.audio_delay_combo.value()) == 125.0
    assert 0.125 in delay_signals


def test_playback_container_set_audio_outputs(qtbot: QtBot, qt_event_loop: QtEventLoop) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    outputs = [
        DummyAudioOutput(0, "Track 1", PrettyChannelsLayout.STEREO),
        DummyAudioOutput(1, "Track 2", PrettyChannelsLayout.SURROUND_5_1),
    ]

    container.set_audio_outputs(outputs, index=1)  # pyright: ignore[reportArgumentType]
    assert container.audio_controls.isEnabled() is True
    assert container.audio_output_combo.count() == 2
    assert container.audio_output_combo.currentIndex() == 1


# Timecode Formatting & FPS Conversions Tests
def test_time_formatting_to_ts() -> None:
    # 0s
    assert Time().to_ts() == "00:00:00.000"
    # 1s
    assert Time(seconds=1).to_ts() == "00:00:01.000"
    # 1m 30s
    assert Time(minutes=1, seconds=30).to_ts() == "00:01:30.000"
    # 1h 16m 41s 500ms
    t = Time(hours=1, minutes=16, seconds=41, milliseconds=500)
    assert t.to_ts() == "01:16:41.500"
    # Custom format strings
    assert t.to_ts("{H}:{M:02d}:{S:02d}") == "1:16:41"
    assert t.to_ts("{S}.{ms:03d}") == "41.500"
    assert t.to_ts("{ts}") == "4601"
    # Millisecond rounding carry boundary tests
    assert Time(seconds=1.9996).to_ts() == "00:00:02.000"
    assert Time(seconds=59.9999).to_ts() == "00:01:00.000"


def test_time_qtime_roundtrip() -> None:
    t = Time(hours=1, minutes=23, seconds=45, milliseconds=678)
    qtime = t.to_qtime()
    assert qtime == QTime(1, 23, 45, 678)

    recovered = Time.from_qtime(qtime)
    assert recovered.to_qtime() == qtime
    assert recovered.total_seconds() == t.total_seconds()


def test_timeline_x_time_and_frame_conversions(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(100, [(i + 1) * 0.04 for i in range(100)])
    timeline = bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 30)

    # x_to_frame
    assert timeline.x_to_frame(0) == Frame(0)
    assert timeline.x_to_frame(500) == Frame(50)
    assert timeline.x_to_frame(1000) == Frame(100)

    # cursor_to_x with Frame
    assert timeline.cursor_to_x(Frame(0)) == 0
    assert timeline.cursor_to_x(Frame(50)) == 500
    assert timeline.cursor_to_x(Frame(100)) == 1000

    # cursor_to_x with Time (2.0s corresponds to frame 50 @ 0.04s per frame)
    assert timeline.cursor_to_x(Time(seconds=2.0)) == 500


def test_generate_label_format_thresholds() -> None:
    # >= 1 hour
    assert generate_label_format(Time(seconds=10), Time(hours=1)) == "{H}:{M:02d}:{S:02d}"
    assert generate_label_format(Time(seconds=10), Time(hours=2)) == "{H}:{M:02d}:{S:02d}"

    # >= 1 minute interval and < 1 hour total
    assert generate_label_format(Time(minutes=1), Time(minutes=30)) == "{M}:{S:02d}"

    # > 10 seconds total and small interval
    assert generate_label_format(Time(seconds=2), Time(seconds=25)) == "{M}:{S:02d}"

    # <= 10 seconds total
    assert generate_label_format(Time(seconds=1), Time(seconds=8)) == "{S}.{ms:03d}"


def test_timeline_mode_switching_frame_time(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    assert timeline.mode == "frame"
    assert timeline.mode_selector.index == 0

    timeline.mode = "time"
    assert timeline.mode == "time"
    assert timeline.mode_selector.index == 1

    # Switch back via segmented control signal
    timeline.mode_selector.segmentChanged.emit(0)
    assert timeline.mode == "frame"
    assert timeline.mode_selector.index == 0


# TimelineControlBar Integration Tests
def test_timeline_control_bar_set_data_synchronization(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    durations = [(i + 1) / 25.0 for i in range(250)]
    bar.set_data(250, durations)

    assert bar.total_frames == 250
    assert bar.total_time == Time(seconds=10.0)

    # Frame spinbox / edit maximums synchronized to 249 (total_frames - 1)
    assert bar.playback_container.frame_edit.maximum() == 249
    assert bar.playback_container.zone_frame_spinbox.maximum() == 249

    # Time edit maximums synchronized to 10 seconds
    assert bar.playback_container.time_edit.maximumTime() == Time(seconds=10.0).to_qtime()
    assert bar.playback_container.zone_time_edit.maximumTime() == Time(seconds=10.0).to_qtime()


def test_timeline_control_bar_playback_controls_enabled(
    timeline_control_bar: TimelineControlBar, qt_event_loop: QtEventLoop
) -> None:
    bar = timeline_control_bar
    pc = bar.playback_container

    bar.set_playback_controls_enabled(False)
    assert pc.seek_n_back_btn.isEnabled() is False
    assert pc.seek_1_back_btn.isEnabled() is False
    assert pc.seek_1_fwd_btn.isEnabled() is False
    assert pc.seek_n_fwd_btn.isEnabled() is False
    assert pc.time_edit.isEnabled() is False
    assert pc.frame_edit.isEnabled() is False
    # Play/pause button remains enabled!
    assert pc.play_pause_btn.isEnabled() is True

    bar.set_playback_controls_enabled(True)
    assert pc.seek_1_fwd_btn.isEnabled() is True
    assert pc.time_edit.isEnabled() is True
    assert pc.frame_edit.isEnabled() is True


def test_timeline_control_bar_disabled_context_manager(
    timeline_control_bar: TimelineControlBar, qt_event_loop: QtEventLoop
) -> None:
    bar = timeline_control_bar

    bar.disable()
    try:
        assert bar.isEnabled() is False
        assert bar.timeline.is_events_blocked is True
    finally:
        bar.enable()

    assert bar.isEnabled() is True
    assert bar.timeline.is_events_blocked is False


def test_frame_edit_reports_the_new_and_the_previous_frame(timeline_control_bar: TimelineControlBar) -> None:
    frame_edit = timeline_control_bar.playback_container.frame_edit
    frame_edit.setValue(40)
    frame_changes: list[tuple[Frame, Frame]] = []
    frame_edit.frameChanged.connect(lambda n, o: frame_changes.append((n, o)))

    frame_edit.setValue(75)

    assert frame_changes == [(Frame(75), Frame(40))]


@pytest.mark.parametrize(
    ("target_x", "expected"), [(1, Frame(1)), (75, Frame(50)), (100, Frame(75)), (900, Frame(500))]
)
def test_frame_notch_interval_is_the_smallest_one_wider_than_the_target(
    timeline_control_bar: TimelineControlBar, target_x: int, expected: Frame
) -> None:
    timeline_control_bar.set_data(500, [(i + 1) / 25.0 for i in range(500)])
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    SettingsManager.global_settings.timeline.notches_margin = 0

    assert timeline.calculate_notch_interval_f(target_x) == expected


@pytest.mark.parametrize(
    ("target_x", "expected"), [(10, Time(seconds=1)), (75, Time(seconds=2)), (800, Time(seconds=30))]
)
def test_time_notch_interval_is_the_smallest_one_longer_than_the_target(
    timeline_control_bar: TimelineControlBar, target_x: int, expected: Time
) -> None:
    timeline_control_bar.set_data(500, [(i + 1) / 25.0 for i in range(500)])
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)
    SettingsManager.global_settings.timeline.notches_margin = 0

    assert timeline.calculate_notch_interval_t(target_x) == expected


@pytest.mark.parametrize(
    "event",
    [QFocusEvent(QEvent.Type.FocusOut), QHideEvent(), QEvent(QEvent.Type.Leave)],
    ids=["focus_out", "hide", "leave"],
)
def test_timeline_clears_the_hover_position_when_the_pointer_or_focus_leaves(
    timeline_control_bar: TimelineControlBar, event: QEvent
) -> None:
    timeline = timeline_control_bar.timeline
    timeline.hover_x = 100

    QApplication.sendEvent(timeline, event)

    assert timeline.hover_x is None


def test_spinbox_focus_animation_is_deleted_when_it_stops(qtbot: QtBot) -> None:
    parent = PlaybackContainer()
    qtbot.addWidget(parent)
    f_edit = FrameEdit(parent)
    qtbot.addWidget(f_edit)

    f_edit.show_focus_for_user()
    assert f_edit.findChildren(QAbstractAnimation) != []

    qtbot.waitUntil(lambda: f_edit.findChildren(QAbstractAnimation) == [], timeout=3000)


@pytest.mark.parametrize(("total_frames", "width"), [(7, 10), (3, 10), (99, 100), (10, 10)])
def test_timeline_frame_to_x_to_frame_round_trip(
    timeline_control_bar: TimelineControlBar, total_frames: int, width: int
) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(total_frames, [(i + 1) / 25.0 for i in range(total_frames)])
    timeline.rect_f = QRectF(0, 0, width, 33)

    round_trip = [int(timeline.x_to_frame(timeline.cursor_to_x(Frame(f)))) for f in range(total_frames)]

    assert round_trip == list(range(total_frames))


def test_frame_edit_validation_clamp_max(qtbot: QtBot) -> None:
    parent = PlaybackContainer()
    qtbot.addWidget(parent)

    f_edit = FrameEdit(parent)
    qtbot.addWidget(f_edit)
    f_edit.setMaximum(100)

    # Valid within bounds
    state, text, pos = f_edit.validate("50", 2)
    assert state == QValidator.State.Acceptable
    assert text == "50"
    assert pos == 2

    # Exceeding maximum snaps to max string
    state, text, pos = f_edit.validate("9999", 4)
    assert state == QValidator.State.Acceptable
    assert text == "100"
    assert pos == 3


def open_context_menu(container: PlaybackContainer, mocker: MockerFixture) -> None:
    mocker.patch.object(container.context_menu, "exec")
    QApplication.sendEvent(
        container,
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5), Qt.KeyboardModifier.NoModifier),
    )


def test_playback_container_context_menu_shows_the_current_settings(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    container = timeline_control_bar.playback_container
    container.settings.seek_step = 7
    container.settings.speed = 4.5
    container.settings.uncapped = True
    container.settings.zone_frames = 30
    container.settings.step = -2
    container.settings.loop = True

    open_context_menu(container, mocker)

    assert container.seek_step_spinbox.value() == 7
    assert container.settings.speed == 4.5
    assert container.speed_slider.value() == 75
    assert container.uncap_checkbox.isChecked() is True
    assert container.speed_slider.isEnabled() is False
    assert container.zone_frame_spinbox.value() == 30
    assert container.step_frame_spinbox.value() == -2
    assert container.loop_checkbox.isChecked() is True


@pytest.mark.parametrize("matches_global", [True, False])
def test_playback_container_context_menu_enables_reset_actions_only_for_changed_values(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture, matches_global: bool
) -> None:
    container = timeline_control_bar.playback_container
    SettingsManager.global_settings.timeline.seek_step = 24
    SettingsManager.global_settings.playback.audio_delay = 0.05
    container.settings.seek_step = 24 if matches_global else 10
    container.audio_delay = 0.05 if matches_global else 0.2

    open_context_menu(container, mocker)

    assert container.reset_seek_step_to_global_action.isEnabled() is not matches_global
    assert container.reset_audio_delay_to_global_action.isEnabled() is not matches_global


def test_playback_container_zone_time_changed(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    pc = bar.playback_container

    # Changing zone_time_edit updates zone_frame_spinbox and settings.zone_frames
    pc.zone_time_edit.setTime(QTime(0, 0, 2, 0))
    assert pc.settings.zone_frames == 50
    assert pc.zone_frame_spinbox.value() == 50


def test_playback_container_audio_delay_reset_to_global(qtbot: QtBot) -> None:
    pc = PlaybackContainer()
    qtbot.addWidget(pc)

    SettingsManager.global_settings.playback.audio_delay = 0.05
    pc.audio_delay = 0.20
    assert pc.audio_delay == 0.20

    # Trigger reset action
    pc.reset_audio_delay_to_global_action.trigger()
    assert pc.audio_delay == 0.05


def test_timeline_click_bounds_clamping(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    total_frames = 100
    bar.set_data(total_frames, [(i + 1) / 25.0 for i in range(total_frames)])
    timeline = bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    image = QImage(1000, 33, QImage.Format.Format_ARGB32_Premultiplied)
    with QPainter(image) as painter:
        timeline.draw_widget(painter)

    emitted_frames = list[Frame]()
    timeline.clicked.connect(lambda f, _t: emitted_frames.append(f))

    scroll_rect = timeline.notches_cache[timeline.mode].value.scroll_rect
    center_y = scroll_rect.center().y()

    # 1. Click at left bound (x = 0)
    event_left = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(0, center_y),
        QPointF(0, center_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mousePressEvent(event_left)
    timeline.mouseReleaseEvent(event_left)

    # 2. Click at right bound (x = width, which without clamping maps to Frame(total_frames))
    width = timeline.rect_f.width()
    assert timeline.x_to_frame(int(width)) == Frame(total_frames)

    event_right = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(width, center_y),
        QPointF(width, center_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    timeline.mousePressEvent(event_right)
    timeline.mouseReleaseEvent(event_right)

    assert emitted_frames == [Frame(0), Frame(total_frames - 1)]


def test_timeline_zero_frame_clips_bounds(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(0)

    assert bar.total_frames == 0
    assert bar.playback_container.frame_edit.maximum() == 0
    assert bar.playback_container.zone_frame_spinbox.maximum() == 1


def test_timeline_custom_notches_line_coordinates_updated_on_scroll_rect_recalculated(
    timeline_control_bar: TimelineControlBar,
) -> None:
    bar = timeline_control_bar
    timeline = bar.timeline
    timeline.rect_f = QRectF(0, 0, 500, 33)

    image1 = QImage(500, 33, QImage.Format.Format_ARGB32_Premultiplied)
    with QPainter(image1) as painter:
        timeline.draw_widget(painter)

    timeline.add_notch("bookmarks", Frame(50), label="Midpoint")
    timeline.add_notch("ranges", Frame(20), end_data=Frame(80), label="Range")

    bookmark_notch = next(n for n in timeline.custom_notches["bookmarks"] if n.data == Frame(50))
    range_notch = next(n for n in timeline.custom_notches["ranges"] if n.data == Frame(20))

    initial_scroll_rect = QRectF(timeline.scroll_rect)
    assert bookmark_notch.line.x1() == 250
    assert bookmark_notch.line.y1() == initial_scroll_rect.top()
    assert bookmark_notch.line.y2() == initial_scroll_rect.top() + initial_scroll_rect.height() - 1

    assert range_notch.line.x1() == 100
    assert range_notch.end_line is not None
    assert range_notch.end_line.x1() == 400

    timeline.rect_f = QRectF(0, 0, 1000, 33)
    image2 = QImage(1000, 33, QImage.Format.Format_ARGB32_Premultiplied)
    with QPainter(image2) as painter:
        timeline.draw_widget(painter)

    new_scroll_rect = QRectF(timeline.scroll_rect)
    assert new_scroll_rect.width() == 1000

    assert bookmark_notch.line.x1() == 500
    assert bookmark_notch.line.x2() == 500
    assert bookmark_notch.line.y1() == new_scroll_rect.top()
    assert bookmark_notch.line.y2() == new_scroll_rect.top() + new_scroll_rect.height() - 1

    assert range_notch.line.x1() == 200
    assert range_notch.end_line is not None
    assert range_notch.end_line.x1() == 800
    assert range_notch.end_line.y1() == new_scroll_rect.top()
    assert range_notch.end_line.y2() == new_scroll_rect.top() + new_scroll_rect.height() - 1
