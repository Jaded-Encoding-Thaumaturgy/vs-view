from __future__ import annotations

import pytest
from PySide6.QtCore import QTime
from pytestqt.qtbot import QtBot

from vsview.app.settings.models import ZoneSettings
from vsview.app.views.zone import ZoneEditor
from vsview.types import Time

pytestmark = [pytest.mark.unit]

CFR_25 = [Time(seconds=(i + 1) / 25) for i in range(100)]


@pytest.mark.parametrize(
    ("settings", "current", "total", "expected"),
    [
        (ZoneSettings(length=4), 2, 10, range(2, 6)),
        (ZoneSettings(length=10), 7, 10, range(7, 10)),
        (ZoneSettings(length=5), 15, 10, range(9, 10)),
        (ZoneSettings(length=3, step=-1), 5, 10, range(5, 2, -1)),
        (ZoneSettings(length=10, step=-1), 5, 10, range(5, -1, -1)),
        (ZoneSettings(length=6, step=2), 0, 10, range(0, 6, 2)),
        (ZoneSettings(start=3, end=6, follow=False), 0, 10, range(3, 7)),
        (ZoneSettings(start=3, end=6, follow=False, step=-2), 9, 10, range(6, 2, -2)),
        (ZoneSettings(start=50, end=150, follow=False), 0, 100, range(50, 100)),
        (ZoneSettings(start=150, end=200, follow=False), 0, 100, range(99, 100)),
    ],
    ids=[
        "follow_forward",
        "follow_clamped_at_last_frame",
        "follow_current_frame_beyond_a_shorter_clip",
        "follow_backward_ends_at_current_frame",
        "follow_backward_clamped_at_first_frame",
        "follow_with_a_step",
        "exact_range_ignores_current_frame",
        "exact_range_backward_starts_at_its_end",
        "exact_range_clamped_to_a_shorter_clip",
        "exact_range_beyond_a_shorter_clip",
    ],
)
def test_zone_frames(settings: ZoneSettings, current: int, total: int, expected: range) -> None:
    assert settings.frames(current, total) == expected


def test_zone_bounds_are_not_stored_clamped_so_a_shorter_clip_keeps_the_zone() -> None:
    settings = ZoneSettings(start=50, end=150, follow=False)

    short = settings.bounds(0, 100)
    long = settings.bounds(0, 200)

    assert (short, long) == ((50, 99), (50, 150))


@pytest.mark.parametrize(
    ("set_end", "frame", "expected"),
    [
        (False, 30, (30, 44, 15)),
        (True, 50, (40, 50, 11)),
        (False, 60, (60, 60, 1)),
        (True, 20, (20, 20, 1)),
    ],
    ids=["start", "end", "start_after_the_end_moves_the_end", "end_before_the_start_moves_the_start"],
)
def test_setting_a_bound_in_follow_mode_freezes_the_range_around_the_current_frame(
    set_end: bool, frame: int, expected: tuple[int, int, int]
) -> None:
    settings = ZoneSettings(length=5)

    if set_end:
        settings.set_end(frame, current=40, total=100)
    else:
        settings.set_start(frame, current=40, total=100)

    assert (settings.start, settings.end, settings.length, settings.follow) == (*expected, False)


def test_leaving_follow_mode_freezes_the_range_around_the_current_frame() -> None:
    settings = ZoneSettings(length=5, step=-1)

    settings.set_follow(False, current=40, total=100)

    assert (settings.start, settings.end, settings.length, settings.follow) == (36, 40, 5, False)


@pytest.mark.parametrize(
    ("follow", "expected"),
    [(True, (10, 20, 50)), (False, (10, 59, 50))],
    ids=["follow_keeps_the_bounds", "exact_moves_the_end"],
)
def test_set_length(follow: bool, expected: tuple[int, int, int]) -> None:
    settings = ZoneSettings(start=10, end=20, length=11, follow=follow)

    settings.set_length(50, total=100)

    assert (settings.start, settings.end, settings.length) == expected


def test_set_length_of_an_exact_range_stops_at_the_last_frame() -> None:
    settings = ZoneSettings(start=90, end=95, follow=False)

    settings.set_length(50, total=100)

    assert (settings.start, settings.end, settings.length) == (90, 99, 10)


@pytest.fixture
def zone_editor(qtbot: QtBot) -> ZoneEditor:
    editor = ZoneEditor(ZoneSettings())
    qtbot.addWidget(editor)
    editor.set_data(100, CFR_25)
    return editor


@pytest.mark.qt
@pytest.mark.parametrize("follow", [True, False])
def test_zone_editor_disables_the_bounds_in_follow_mode(zone_editor: ZoneEditor, follow: bool) -> None:
    zone_editor.follow_checkbox.setChecked(follow)

    bounds_widgets = (
        zone_editor.start_time_edit,
        zone_editor.start_frame_edit,
        zone_editor.start_here_btn,
        zone_editor.end_time_edit,
        zone_editor.end_frame_edit,
        zone_editor.end_here_btn,
    )
    assert [w.isEnabled() for w in bounds_widgets] == [not follow] * len(bounds_widgets)
    assert zone_editor.length_frame_edit.isEnabled() is True


@pytest.mark.qt
def test_zone_editor_shows_the_bounds_around_the_current_frame_in_follow_mode(zone_editor: ZoneEditor) -> None:
    zone_editor.length_frame_edit.setValue(10)

    zone_editor.refresh(current=40)

    assert (zone_editor.start_frame_edit.value(), zone_editor.end_frame_edit.value()) == (40, 49)
    assert zone_editor.start_time_edit.time() == QTime(0, 0, 1, 600)
    assert zone_editor.end_time_edit.time() == QTime(0, 0, 1, 960)
    assert zone_editor.length_time_edit.time() == QTime(0, 0, 0, 400)


@pytest.mark.qt
def test_zone_editor_start_time_edit_sets_the_start_frame(zone_editor: ZoneEditor) -> None:
    zone_editor.follow_checkbox.setChecked(False)

    zone_editor.start_time_edit.setTime(QTime(0, 0, 2, 0))

    assert zone_editor.settings.start == 50
    assert zone_editor.start_frame_edit.value() == 50


@pytest.mark.qt
def test_zone_editor_length_time_is_measured_from_the_zone_start(qtbot: QtBot) -> None:
    # Frames 0-9 last 1 s each, then frames 10-99 last 0.1 s each.
    vfr = [Time(seconds=i + 1) for i in range(10)] + [Time(seconds=10 + (i + 1) / 10) for i in range(90)]
    editor = ZoneEditor(ZoneSettings(start=10, end=10, follow=False))
    qtbot.addWidget(editor)
    editor.set_data(100, vfr)

    editor.length_time_edit.setTime(QTime(0, 0, 1, 0))

    assert (editor.settings.start, editor.settings.end, editor.settings.length) == (10, 19, 10)


@pytest.mark.qt
def test_zone_editor_here_buttons_use_the_current_frame(zone_editor: ZoneEditor, qtbot: QtBot) -> None:
    zone_editor.follow_checkbox.setChecked(False)
    zone_editor.refresh(current=30)
    zone_editor.start_here_btn.click()
    zone_editor.refresh(current=45)

    zone_editor.end_here_btn.click()

    assert (zone_editor.settings.start, zone_editor.settings.end) == (30, 45)
    assert zone_editor.length_frame_edit.value() == 16


@pytest.mark.qt
def test_zone_editor_step_skips_zero(zone_editor: ZoneEditor) -> None:
    steps = list[int]()

    for value in (0, 0, 5):
        zone_editor.step_spinbox.setValue(value)
        steps.append(zone_editor.settings.step)

    assert steps == [-1, 1, 5]


@pytest.mark.qt
def test_zone_editor_from_cache_uses_the_cache_range(zone_editor: ZoneEditor) -> None:
    zone_editor.set_cache_state(range(20, 31), None)

    zone_editor.from_cache_btn.click()

    s = zone_editor.settings
    assert (s.start, s.end, s.length, s.follow) == (20, 30, 11, False)
    assert zone_editor.follow_checkbox.isChecked() is False


@pytest.mark.qt
@pytest.mark.parametrize(
    ("frames", "reason", "expected"),
    [(None, None, (False, True)), (range(5, 10), None, (True, True)), (None, "disabled", (False, False))],
    ids=["no_cache_range", "full_cache_range", "cache_unavailable"],
)
def test_zone_editor_cache_buttons_follow_the_cache_state(
    zone_editor: ZoneEditor, frames: range | None, reason: str | None, expected: tuple[bool, bool]
) -> None:
    zone_editor.set_cache_state(frames, reason)

    assert (zone_editor.from_cache_btn.isEnabled(), zone_editor.to_cache_btn.isEnabled()) == expected
