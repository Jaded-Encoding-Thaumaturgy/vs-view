from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import Shiboken
from vsengine.loops import Cancelled

from vsview.app.workspace.lifecycle import Phase, StaleGenerationError, WorkspaceLifecycle

pytestmark = [pytest.mark.unit]


def _lifecycle_in(phase: Phase) -> WorkspaceLifecycle:
    lifecycle = WorkspaceLifecycle()

    match phase:
        case Phase.EMPTY:
            pass
        case Phase.LOADING:
            lifecycle.try_begin_load()
        case Phase.RENDERING:
            lifecycle.try_begin_load()
            lifecycle.begin_rendering()
        case Phase.LOADED:
            lifecycle.try_begin_load()
            generation = lifecycle.next_generation()
            lifecycle.begin_rendering()
            lifecycle.finish(generation)
        case Phase.REPLACING:
            lifecycle.try_begin_load()
            generation = lifecycle.next_generation()
            lifecycle.begin_rendering()
            lifecycle.finish(generation)
            lifecycle.try_begin_reload()
        case Phase.FAILED:
            lifecycle.try_begin_load()
            lifecycle.fail()
        case Phase.CLOSING:
            lifecycle.begin_close()
        case Phase.CLOSED:
            lifecycle.begin_close()
            lifecycle.mark_closed()

    assert lifecycle.phase is phase
    return lifecycle


@pytest.mark.parametrize(
    ("start", "entered"),
    [
        (Phase.EMPTY, Phase.LOADING),
        (Phase.LOADING, None),
        (Phase.RENDERING, None),
        (Phase.LOADED, Phase.REPLACING),
        (Phase.REPLACING, None),
        (Phase.FAILED, Phase.LOADING),
        (Phase.CLOSING, None),
        (Phase.CLOSED, None),
    ],
)
def test_try_begin_load(start: Phase, entered: Phase | None) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.try_begin_load()

    assert result is entered
    assert lifecycle.phase is (start if entered is None else entered)


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.RENDERING, False, Phase.RENDERING),
        (Phase.LOADED, True, Phase.REPLACING),
        (Phase.REPLACING, False, Phase.REPLACING),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_try_begin_reload(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.try_begin_reload()

    assert result is accepted
    assert lifecycle.phase is end


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, True, Phase.FAILED),
        (Phase.LOADING, True, Phase.FAILED),
        (Phase.RENDERING, True, Phase.FAILED),
        (Phase.LOADED, True, Phase.FAILED),
        (Phase.REPLACING, True, Phase.FAILED),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_fail(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.fail()

    assert result is accepted
    assert lifecycle.phase is end


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.RENDERING, False, Phase.RENDERING),
        (Phase.LOADED, True, Phase.FAILED),
        (Phase.REPLACING, False, Phase.REPLACING),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_fail_loaded_only_fails_nothing_but_loaded_content(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.fail(loaded_only=True)

    assert result is accepted
    assert lifecycle.phase is end


def test_finish_marks_the_current_generation_as_loaded() -> None:
    lifecycle = _lifecycle_in(Phase.RENDERING)
    generation = lifecycle.next_generation()

    result = lifecycle.finish(generation)

    assert result is True
    assert lifecycle.phase is Phase.LOADED


def test_an_older_generation_cannot_finish() -> None:
    lifecycle = WorkspaceLifecycle()
    lifecycle.try_begin_load()
    older = lifecycle.next_generation()
    newer = lifecycle.next_generation()
    lifecycle.begin_rendering()

    older_result = lifecycle.finish(older)
    phase_after_older = lifecycle.phase
    newer_result = lifecycle.finish(newer)

    assert older_result is False
    assert phase_after_older is Phase.RENDERING
    assert newer_result is True
    assert lifecycle.phase is Phase.LOADED


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.RENDERING, False, Phase.RENDERING),
        (Phase.LOADED, False, Phase.LOADED),
        (Phase.REPLACING, True, Phase.LOADED),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_cancel_reload(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    generation_before = lifecycle.generation

    result = lifecycle.cancel_reload()

    assert result is accepted
    assert lifecycle.phase is end
    assert lifecycle.generation == generation_before


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.RENDERING, False, Phase.RENDERING),
        (Phase.LOADED, False, Phase.LOADED),
        (Phase.REPLACING, True, Phase.LOADING),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_commit_replacement(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.commit_replacement()

    assert result is accepted
    assert lifecycle.phase is end


def test_a_committed_replacement_can_no_longer_keep_the_loaded_content() -> None:
    lifecycle = _lifecycle_in(Phase.REPLACING)
    lifecycle.commit_replacement()

    kept = lifecycle.cancel_reload()

    assert kept is False
    assert lifecycle.phase is Phase.LOADING


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, True, Phase.RENDERING),
        (Phase.RENDERING, False, Phase.RENDERING),
        (Phase.LOADED, False, Phase.LOADED),
        (Phase.REPLACING, False, Phase.REPLACING),
        (Phase.FAILED, False, Phase.FAILED),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_begin_rendering(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.begin_rendering()

    assert result is accepted
    assert lifecycle.phase is end


@pytest.mark.parametrize(
    "start", [Phase.EMPTY, Phase.LOADING, Phase.LOADED, Phase.REPLACING, Phase.FAILED, Phase.CLOSING, Phase.CLOSED]
)
def test_finish_outside_a_render_changes_nothing(start: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    generation = lifecycle.next_generation()

    result = lifecycle.finish(generation)

    assert result is False
    assert lifecycle.phase is start


@pytest.mark.parametrize(
    "start", [Phase.EMPTY, Phase.LOADING, Phase.RENDERING, Phase.LOADED, Phase.REPLACING, Phase.FAILED]
)
def test_begin_close_makes_every_older_generation_stale(start: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    older = lifecycle.next_generation()

    closing = lifecycle.begin_close()

    assert closing is not None
    assert closing.is_current() is True
    assert older.is_current() is False
    assert lifecycle.phase is Phase.CLOSING
    assert lifecycle.closing is True


@pytest.mark.parametrize("start", [Phase.CLOSING, Phase.CLOSED])
def test_begin_close_runs_only_once(start: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    generation_before = lifecycle.generation

    result = lifecycle.begin_close()

    assert result is None
    assert lifecycle.generation == generation_before
    assert lifecycle.phase is start


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADED, False, Phase.LOADED),
        (Phase.CLOSING, True, Phase.CLOSED),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_mark_closed(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.mark_closed()

    assert result is accepted
    assert lifecycle.phase is end


@pytest.mark.parametrize(
    ("start", "closing"),
    [
        (Phase.EMPTY, False),
        (Phase.LOADING, False),
        (Phase.RENDERING, False),
        (Phase.LOADED, False),
        (Phase.REPLACING, False),
        (Phase.FAILED, False),
        (Phase.CLOSING, True),
        (Phase.CLOSED, True),
    ],
)
def test_closing(start: Phase, closing: bool) -> None:
    lifecycle = _lifecycle_in(start)

    assert lifecycle.closing is closing


def test_generation_check_raises_a_cancellation_once_stale() -> None:
    lifecycle = WorkspaceLifecycle()
    generation = lifecycle.next_generation()
    generation.check()

    lifecycle.next_generation()

    with pytest.raises(StaleGenerationError, match=rf"Generation {generation.value} is stale") as exc_info:
        generation.check()
    assert isinstance(exc_info.value, Cancelled)


@pytest.mark.qt
@pytest.mark.parametrize(
    ("start", "held"),
    [
        (Phase.EMPTY, True),
        (Phase.LOADING, False),
        (Phase.RENDERING, True),
        (Phase.LOADED, True),
        (Phase.REPLACING, False),
        (Phase.FAILED, True),
        (Phase.CLOSING, False),
        (Phase.CLOSED, False),
    ],
)
def test_try_hold(qapp: QApplication, start: Phase, held: bool) -> None:
    lifecycle = _lifecycle_in(start)
    holder = QObject()

    result = lifecycle.try_hold(holder)

    assert result is held
    assert lifecycle.busy is held


@pytest.mark.qt
@pytest.mark.parametrize(
    "begin",
    [
        pytest.param(WorkspaceLifecycle.try_begin_reload, id="reload"),
        pytest.param(WorkspaceLifecycle.try_begin_load, id="load-replacing-content"),
    ],
)
def test_loaded_content_is_not_replaced_while_a_plugin_holds_the_workspace(
    qapp: QApplication, begin: Callable[[WorkspaceLifecycle], object]
) -> None:
    lifecycle = _lifecycle_in(Phase.LOADED)
    holder = QObject()
    lifecycle.try_hold(holder)

    begin(lifecycle)
    phase_while_held = lifecycle.phase
    lifecycle.release_hold(holder)
    begin(lifecycle)

    assert phase_while_held is Phase.LOADED
    assert lifecycle.phase is Phase.REPLACING


@pytest.mark.qt
def test_plugins_can_hold_the_workspace_again_once_the_new_outputs_exist(qapp: QApplication) -> None:
    lifecycle = WorkspaceLifecycle()
    holder = QObject()
    lifecycle.try_begin_load()

    held_while_loading = lifecycle.try_hold(holder)
    lifecycle.begin_rendering()
    held_after = lifecycle.try_hold(holder)

    assert held_while_loading is False
    assert held_after is True
    assert lifecycle.busy is True


@pytest.mark.qt
def test_a_destroyed_holder_releases_the_workspace(qapp: QApplication) -> None:
    lifecycle = _lifecycle_in(Phase.LOADED)
    holder = QObject()
    lifecycle.try_hold(holder)

    Shiboken.delete(holder)

    assert lifecycle.busy is False
    assert lifecycle.try_begin_reload() is True
