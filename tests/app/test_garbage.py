from __future__ import annotations

import gc
import threading
from collections.abc import Callable, Generator

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from vsview.app.garbage import GarbageCollector, collect_garbage

pytestmark = [pytest.mark.unit, pytest.mark.qt]

type Counts = Callable[[], object]


@pytest.fixture
def set_gc_state() -> Generator[Callable[[tuple[int, int, int], Counts], None]]:
    thresholds = gc.get_threshold()
    alive = list[list[object]]()

    def apply(new_thresholds: tuple[int, int, int], set_counts: Counts) -> None:
        gc.collect()
        set_counts()
        gc.set_threshold(*new_thresholds)
        # The collections above reset the young generation count, and collect_due only runs when it is above 0
        alive.extend([] for _ in range(10))

    try:
        yield apply
    finally:
        gc.set_threshold(*thresholds)


def _make_full_collection_due(set_gc_state: Callable[[tuple[int, int, int], Counts], None]) -> None:
    set_gc_state((0, 0, 0), lambda: gc.collect(1))


@pytest.mark.parametrize(
    ("thresholds", "set_counts", "expected"),
    [
        pytest.param((10**9, 0, 0), lambda: None, None, id="nothing-due"),
        pytest.param((0, 10, 10), lambda: None, 0, id="young"),
        pytest.param((0, 0, 10), lambda: gc.collect(0), 1, id="middle"),
        pytest.param((0, 0, 0), lambda: gc.collect(1), 2, id="full"),
    ],
)
def test_collect_due_collects_the_oldest_generation_over_its_threshold(
    set_gc_state: Callable[[tuple[int, int, int], Counts], None],
    thresholds: tuple[int, int, int],
    set_counts: Counts,
    expected: int | None,
) -> None:
    set_gc_state(thresholds, set_counts)

    assert GarbageCollector.collect_due() == expected


def test_deferred_full_collection_collects_the_middle_generation(
    set_gc_state: Callable[[tuple[int, int, int], Counts], None],
) -> None:
    owner = QObject()
    _make_full_collection_due(set_gc_state)
    GarbageCollector.defer_full_collections(owner)

    try:
        generation = GarbageCollector.collect_due()
    finally:
        GarbageCollector.resume_full_collections(owner)

    assert generation == 1


@pytest.mark.parametrize(
    "release",
    [
        pytest.param(GarbageCollector.resume_full_collections, id="resumed"),
        pytest.param(lambda owner: Shiboken.delete(owner), id="owner-deleted"),
    ],
)
def test_released_deferral_allows_full_collection(
    set_gc_state: Callable[[tuple[int, int, int], Counts], None],
    release: Callable[[QObject], object],
) -> None:
    owner = QObject()
    GarbageCollector.defer_full_collections(owner)
    _make_full_collection_due(set_gc_state)

    release(owner)

    assert GarbageCollector.collect_due() == 2


def test_full_collection_runs_once_deferred_for_too_long(
    set_gc_state: Callable[[tuple[int, int, int], Counts], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = QObject()
    monkeypatch.setattr(GarbageCollector, "MAX_FULL_DEFERRAL_S", 0.0)
    _make_full_collection_due(set_gc_state)
    GarbageCollector.defer_full_collections(owner)

    try:
        generation = GarbageCollector.collect_due()
    finally:
        GarbageCollector.resume_full_collections(owner)

    assert generation == 2


@pytest.mark.usefixtures("qt_event_loop")
def test_collect_garbage_from_worker_collects_on_main_thread(qtbot: QtBot) -> None:
    collecting_threads = list[threading.Thread]()

    def record_thread(phase: str, _: dict[str, int]) -> None:
        if phase == "start":
            collecting_threads.append(threading.current_thread())

    gc.callbacks.append(record_thread)
    try:
        worker = threading.Thread(target=collect_garbage, daemon=True)
        worker.start()
        qtbot.waitUntil(lambda: not worker.is_alive(), timeout=2000)
    finally:
        gc.callbacks.remove(record_thread)

    assert collecting_threads == [threading.main_thread()]
