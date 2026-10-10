from __future__ import annotations

from typing import Any, NamedTuple

import pytest
from jetpytools import complex_hash
from PySide6.QtCore import QEvent, QLineF, QPoint, QPointF, QRectF, QSize, Qt, QTime
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFocusEvent,
    QFontMetrics,
    QHideEvent,
    QImage,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QValidator,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QSlider, QToolButton
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.app.outputs.audio import PrettyChannelsLayout
from vsview.app.settings import SettingsManager
from vsview.app.views.timeline import (
    CustomNotch,
    FrameEdit,
    HoverLabel,
    Notch,
    PlaybackContainer,
    PlaybackSettings,
    StepSpinBox,
    TimeEdit,
    Timeline,
    TimelineControlBar,
    generate_label_format,
)
from vsview.types import Frame, Time
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class DummyAudioOutput(NamedTuple):
    vs_index: int
    vs_name: str
    chanels_layout: PrettyChannelsLayout


@pytest.fixture
def timeline_control_bar(qtbot: QtBot) -> TimelineControlBar:
    """Provide a TimelineControlBar with 100 frames initialized."""
    bar = TimelineControlBar()
    qtbot.addWidget(bar)
    qtbot.addWidget(bar.timeline.hover_popup)
    bar.resize(1000, 33)
    bar.set_data(100)
    return bar


# Notch & CustomNotch Tests
def test_notch_creation_defaults() -> None:
    notch = Notch(Frame(10))
    assert notch.data == Frame(10)
    assert notch.end_data is None
    assert notch.color == QColor(Qt.GlobalColor.black)
    assert notch.line == QLineF()
    assert notch.end_line is None
    assert notch.label == ""


def test_notch_creation_custom_attributes() -> None:
    line1 = QLineF(10, 0, 10, 20)
    line2 = QLineF(50, 0, 50, 20)
    notch = Notch(
        data=Time(seconds=5),
        end_data=Time(seconds=15),
        color="red",
        line=line1,
        end_line=line2,
        label="Test Segment",
    )
    assert notch.data == Time(seconds=5)
    assert notch.end_data == Time(seconds=15)
    assert notch.color == QColor("red")
    assert notch.line == line1
    assert notch.end_line == line2
    assert notch.label == "Test Segment"


def test_notch_draw_single_line_and_cosmetic() -> None:
    image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    notch = Notch(Frame(5), color=QColor(255, 0, 0), line=QLineF(50, 10, 50, 90))
    scroll_rect = QRectF(0, 0, 100, 100)

    with QPainter(image) as painter:
        notch.draw(painter, scroll_rect, cosmetic=True)
        assert painter.pen().isCosmetic() is True

    # Check non-cosmetic
    with QPainter(image) as painter:
        notch.draw(painter, scroll_rect, cosmetic=False)
        assert painter.pen().isCosmetic() is False


def test_notch_draw_range_fill() -> None:
    image = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    notch = Notch(
        Frame(10),
        end_data=Frame(30),
        color=QColor(0, 0, 255),
        line=QLineF(20, 10, 20, 90),
        end_line=QLineF(80, 10, 80, 90),
        label="Range",
    )
    scroll_rect = QRectF(0, 10, 200, 80)

    with QPainter(image) as painter:
        notch.draw(painter, scroll_rect, range_alpha=120)
        # Painter executed without throwing, pen set to notch color
        assert painter.pen().color() == QColor(0, 0, 255)


def test_custom_notch_creation_and_attributes() -> None:
    cnotch = CustomNotch(
        id="scene_001",
        data=Frame(42),
        color=QColor(0, 255, 0),
        label="Intro",
    )
    assert cnotch.id == "scene_001"
    assert cnotch.data == Frame(42)
    assert cnotch.label == "Intro"
    assert cnotch.color == QColor(0, 255, 0)


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


def test_custom_notch_complex_hash_integration() -> None:
    h1 = complex_hash.hash("bookmarks", Frame(50), None)
    h2 = complex_hash.hash("bookmarks", Frame(50), None)
    h3 = complex_hash.hash("bookmarks", Frame(51), None)

    assert h1 == h2
    assert h1 != h3

    notch1 = CustomNotch(h1, Frame(50))
    notch2 = CustomNotch(h2, Frame(50))

    notch_set = {notch1}
    assert notch2 in notch_set
    notch_set.discard(notch2)
    assert len(notch_set) == 0


# TimelineHoverPopup Tests
def test_hover_popup_instantiation(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    popup = timeline.hover_popup

    assert popup.parent() is None
    assert (popup.windowFlags() & Qt.WindowType.ToolTip) == Qt.WindowType.ToolTip
    assert (popup.windowFlags() & Qt.WindowType.FramelessWindowHint) == Qt.WindowType.FramelessWindowHint
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True

    assert popup.radius == 50
    assert popup.zoom_factor == 4.0
    assert popup.hover_x == -1


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


def test_hover_popup_notch_label_and_value_display(
    timeline_control_bar: TimelineControlBar, mocker: MockerFixture
) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    popup = timeline.hover_popup
    popup.update_state(200)

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

    assert popup._apply_staggering([], fm) == []

    # Two labels far apart: both offset 0
    labels = [
        HoverLabel(text="A", x=10.0, color=QColor("red")),
        HoverLabel(text="B", x=300.0, color=QColor("blue")),
    ]
    staggered = popup._apply_staggering(labels, fm)
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
    staggered_crowded = popup._apply_staggering(crowded, fm)
    offsets = [lbl.y_offset for lbl in staggered_crowded]
    assert offsets == [0, -20, -40, 0]


def test_hover_popup_paint_event_negative_hover(timeline_control_bar: TimelineControlBar) -> None:
    popup = timeline_control_bar.timeline.hover_popup
    popup.hover_x = -1

    image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)

    # Calling paintEvent with hover_x < 0 returns early
    event = QPaintEvent(QRectF(0, 0, 100, 100).toRect())
    popup.paintEvent(event)


def test_hover_popup_paint_event_active(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline_control_bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    timeline.rect_f = QRectF(0, 0, 800, 33)

    popup = timeline.hover_popup
    popup.update_state(200)

    timeline.add_notch("bookmarks", Frame(20), label="Mark 20")
    timeline.add_notch("bookmarks", Frame(25), end_data=Frame(35), label="Range 25-35")

    # Render widget without errors in frame mode
    popup.paintEvent(QPaintEvent(popup.rect()))

    # Now in time mode
    timeline.mode = "time"
    popup.paintEvent(QPaintEvent(popup.rect()))


# Timeline widget (Scrubber Slider) Tests
def test_timeline_frame_positioning_and_cursor(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    timeline.cursor_x = Frame(25)
    assert timeline.x_to_frame(timeline.cursor_x) == Frame(25)
    assert timeline.cursor_x == 250

    timeline.cursor_x = Frame(50)
    assert timeline.x_to_frame(timeline.cursor_x) == Frame(50)
    assert timeline.cursor_x == 500


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
        timeline._draw_widget(painter)

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


def test_timeline_display_scale_settings_reaction(
    timeline_control_bar: TimelineControlBar, qt_event_loop: QtEventLoop
) -> None:
    timeline = timeline_control_bar.timeline

    SettingsManager.global_settings.timeline.display_scale = 1.75
    SettingsManager.signals.globalChanged.emit()
    assert timeline.display_scale == 1.75

    SettingsManager.global_settings.timeline.display_scale = 2.5
    SettingsManager.signals.globalChanged.emit()
    assert timeline.display_scale == 2.5

    SettingsManager.global_settings.timeline.display_scale = 1.0
    SettingsManager.signals.globalChanged.emit()
    assert timeline.display_scale == 1.0


def test_timeline_hover_zoom_factor_scaling(timeline_control_bar: TimelineControlBar) -> None:
    popup = timeline_control_bar.timeline.hover_popup

    # Update hover zoom factor within valid bounds
    SettingsManager.global_settings.timeline.hover_zoom_factor = 12.0
    popup.update_state(100)
    assert popup.zoom_factor == 12.0

    # Bounds in Settings: min 1.0, max 20.0
    SettingsManager.global_settings.timeline.hover_zoom_factor = 20.0
    popup.update_state(100)
    assert popup.zoom_factor == 20.0


def test_timeline_block_events_and_reset_interaction(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.mousepressed = True
    timeline.hover_x = 100

    timeline.reset_interaction()
    assert timeline.mousepressed is False
    assert timeline.hover_x is None

    # Context manager block_events
    with timeline.block_events():
        assert timeline.is_events_blocked is True
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

    assert timeline.is_events_blocked is False


# PlaybackContainer Tests
def test_playback_container_instantiation_defaults(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    assert isinstance(container.settings, PlaybackSettings)
    assert container.settings.seek_step == 1
    assert container.settings.speed == 1.0
    assert container.settings.uncapped is False
    assert container.settings.zone_frames == 100
    assert container.settings.loop is False
    assert container.settings.step == 1

    assert isinstance(container.seek_1_fwd_btn, QToolButton)
    assert isinstance(container.seek_1_back_btn, QToolButton)
    assert isinstance(container.seek_n_fwd_btn, QToolButton)
    assert isinstance(container.seek_n_back_btn, QToolButton)
    assert isinstance(container.play_pause_btn, QToolButton)
    assert isinstance(container.time_edit, TimeEdit)
    assert isinstance(container.frame_edit, FrameEdit)
    assert isinstance(container.volume_slider, QSlider)

    assert container.seek_1_fwd_btn.parent() is container
    assert container.seek_1_back_btn.parent() is container
    assert container.seek_n_fwd_btn.parent() is container
    assert container.seek_n_back_btn.parent() is container
    assert container.play_pause_btn.parent() is container
    assert container.time_edit.parent() is container
    assert container.frame_edit.parent() is container
    assert container.volume_slider.parent() is container.audio_controls
    assert container.isAncestorOf(container.volume_slider) is True


def test_playback_container_seek_buttons(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    clicks = {"fwd_1": 0, "back_1": 0, "fwd_n": 0, "back_n": 0}

    container.seek_1_fwd_btn.clicked.connect(lambda: clicks.__setitem__("fwd_1", clicks["fwd_1"] + 1))
    container.seek_1_back_btn.clicked.connect(lambda: clicks.__setitem__("back_1", clicks["back_1"] + 1))
    container.seek_n_fwd_btn.clicked.connect(lambda: clicks.__setitem__("fwd_n", clicks["fwd_n"] + 1))
    container.seek_n_back_btn.clicked.connect(lambda: clicks.__setitem__("back_n", clicks["back_n"] + 1))

    qtbot.mouseClick(container.seek_1_fwd_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(container.seek_1_back_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(container.seek_n_fwd_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(container.seek_n_back_btn, Qt.MouseButton.LeftButton)

    assert clicks["fwd_1"] == 1
    assert clicks["back_1"] == 1
    assert clicks["fwd_n"] == 1
    assert clicks["back_n"] == 1


def test_playback_container_play_pause_toggling(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    assert container.play_pause_btn.isCheckable() is True
    assert container.play_pause_btn.isChecked() is False

    qtbot.mouseClick(container.play_pause_btn, Qt.MouseButton.LeftButton)
    assert container.play_pause_btn.isChecked() is True

    qtbot.mouseClick(container.play_pause_btn, Qt.MouseButton.LeftButton)
    assert container.play_pause_btn.isChecked() is False


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


def test_playback_container_speed_slider_and_reset(qtbot: QtBot) -> None:
    container = PlaybackContainer()
    qtbot.addWidget(container)

    # Value 50 corresponds to 1.0x
    container.speed_slider.setValue(50)
    assert container.settings.speed == 1.0
    assert container.speed_slider.toolTip() == "1.00x"

    # Minimum slider (0) -> 0.25x
    container.speed_slider.setValue(0)
    assert container.settings.speed == 0.25
    assert container.speed_slider.toolTip() == "0.25x"

    # Maximum slider (100) -> 8.0x
    container.speed_slider.setValue(100)
    assert container.settings.speed == 8.0
    assert container.speed_slider.toolTip() == "8.00x"

    container.speed_slider.setValue(25)  # 0.625 -> rounded to 0.75x or 0.5x
    assert container.settings.speed > 0.25

    # Reset button
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
    assert False in mute_signals


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
def test_timeline_control_bar_integration(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    assert isinstance(bar.timeline, Timeline)
    assert isinstance(bar.playback_container, PlaybackContainer)

    assert bar.timeline.parent() is bar
    assert bar.playback_container.parent() is bar
    assert bar.total_frames == 100


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

    with bar.disabled():
        assert bar.isEnabled() is False
        assert bar.timeline.is_events_blocked is True

    assert bar.isEnabled() is True
    assert bar.timeline.is_events_blocked is False


def test_timeline_control_bar_event_synchronization(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(100, [(i + 1) / 25.0 for i in range(100)])
    bar.timeline.rect_f = QRectF(0, 0, 1000, 33)

    # Synchronize frame_edit change to timeline cursor
    bar.playback_container.frame_edit.setValue(40)
    bar.timeline.cursor_x = Frame(bar.playback_container.frame_edit.value())
    assert bar.timeline.x_to_frame(bar.timeline.cursor_x) == Frame(40)

    # FrameEdit emission
    frame_changes: list[tuple[Frame, Frame]] = []
    bar.playback_container.frame_edit.frameChanged.connect(lambda n, o: frame_changes.append((n, o)))
    bar.playback_container.frame_edit.setValue(75)
    assert len(frame_changes) == 1
    assert frame_changes[0] == (Frame(75), Frame(40))


def test_timeline_parentless_properties_raise_not_implemented(qtbot: QtBot) -> None:
    tl = Timeline()
    qtbot.addWidget(tl)
    qtbot.addWidget(tl.hover_popup)
    with pytest.raises(NotImplementedError, match=r"^$"):
        _ = tl.total_frames
    with pytest.raises(NotImplementedError, match=r"^$"):
        _ = tl.total_time
    with pytest.raises(NotImplementedError, match=r"^$"):
        _ = tl.cum_durations


def test_timeline_draw_widget_time_mode_and_hover(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(120, [(i + 1) / 24.0 for i in range(120)])
    timeline = bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    timeline.mode = "time"
    timeline.hover_x = 250

    image = QImage(1000, 33, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black)
    with QPainter(image) as painter:
        timeline._draw_widget(painter)

    # In frame mode with hover
    timeline.mode = "frame"
    timeline.hover_x = 500
    with QPainter(image) as painter:
        timeline._draw_widget(painter)


def test_timeline_calculate_notch_intervals(timeline_control_bar: TimelineControlBar) -> None:
    bar = timeline_control_bar
    bar.set_data(500, [(i + 1) / 25.0 for i in range(500)])
    timeline = bar.timeline
    timeline.rect_f = QRectF(0, 0, 1000, 33)

    interval_f = timeline.calculate_notch_interval_f(75)
    assert interval_f in Timeline.NOTCH_INTERVALS_F

    interval_t = timeline.calculate_notch_interval_t(75)
    assert interval_t in Timeline.NOTCH_INTERVALS_T


def test_timeline_events_move_leave_resize_focus_hide(timeline_control_bar: TimelineControlBar) -> None:
    timeline = timeline_control_bar.timeline
    timeline.hover_x = 100

    # Move event triggers update
    timeline.moveEvent(QMoveEvent(QPoint(10, 10), QPoint(0, 0)))

    # Resize event triggers update
    timeline.resizeEvent(QResizeEvent(QSize(800, 33), QSize(1000, 33)))

    # Focus out resets interaction
    timeline.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert timeline.hover_x is None

    timeline.hover_x = 100
    # Hide event resets interaction
    timeline.hideEvent(QHideEvent())
    assert timeline.hover_x is None

    timeline.hover_x = 100
    # Leave event clears hover_x and hides popup
    timeline.leaveEvent(QEvent(QEvent.Type.Leave))
    assert timeline.hover_x is None


def test_spinbox_show_focus_for_user(qtbot: QtBot) -> None:
    parent = PlaybackContainer()
    qtbot.addWidget(parent)

    f_edit = FrameEdit(parent)
    t_edit = TimeEdit(parent)
    qtbot.addWidget(f_edit)
    qtbot.addWidget(t_edit)

    # Calling show_focus_for_user creates animation and sets graphics drop shadow effect
    f_edit.show_focus_for_user()
    t_edit.show_focus_for_user()
    assert isinstance(f_edit.graphicsEffect(), QGraphicsDropShadowEffect)
    assert isinstance(t_edit.graphicsEffect(), QGraphicsDropShadowEffect)


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


def test_playback_container_mute_icon_levels(qtbot: QtBot) -> None:
    pc = PlaybackContainer()
    qtbot.addWidget(pc)

    # 0 volume
    pc.volume = 0.0
    assert pc.raw_volume == 0.0

    # Low volume (< 0.33)
    pc.volume = 0.2
    assert pytest.approx(pc.raw_volume) == 0.2

    # Mid volume (>= 0.33, < 0.67)
    pc.volume = 0.5
    assert pytest.approx(pc.raw_volume) == 0.5

    # High volume (>= 0.67)
    pc.volume = 0.9
    assert pytest.approx(pc.raw_volume) == 0.9


def test_playback_container_context_menu_event(
    timeline_control_bar: TimelineControlBar, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc = timeline_control_bar.playback_container
    exec_called = False

    def fake_exec(*_args: Any, **_kwargs: Any) -> None:
        nonlocal exec_called
        exec_called = True

    monkeypatch.setattr(pc.context_menu, "exec", fake_exec)

    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        QPoint(50, 10),
        QPoint(100, 100),
        Qt.KeyboardModifier.NoModifier,
    )
    pc.contextMenuEvent(event)
    assert exec_called is True


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
