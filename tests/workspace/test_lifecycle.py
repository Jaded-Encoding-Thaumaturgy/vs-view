from __future__ import annotations

import pytest
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
        case Phase.LOADED:
            lifecycle.try_begin_load()
            lifecycle.finish(lifecycle.next_generation())
        case Phase.RELOADING:
            lifecycle.try_begin_load()
            lifecycle.finish(lifecycle.next_generation())
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
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, True, Phase.LOADING),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.LOADED, True, Phase.RELOADING),
        (Phase.RELOADING, False, Phase.RELOADING),
        (Phase.FAILED, True, Phase.LOADING),
        (Phase.CLOSING, False, Phase.CLOSING),
        (Phase.CLOSED, False, Phase.CLOSED),
    ],
)
def test_try_begin_load(start: Phase, accepted: bool, end: Phase) -> None:
    lifecycle = _lifecycle_in(start)

    result = lifecycle.try_begin_load()

    assert result is accepted
    assert lifecycle.phase is end


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.LOADED, True, Phase.RELOADING),
        (Phase.RELOADING, False, Phase.RELOADING),
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
        (Phase.LOADED, True, Phase.FAILED),
        (Phase.RELOADING, True, Phase.FAILED),
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


@pytest.mark.parametrize("start", [Phase.LOADING, Phase.RELOADING])
def test_finish_marks_the_current_generation_as_loaded(start: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    generation = lifecycle.next_generation()

    result = lifecycle.finish(generation)

    assert result is True
    assert lifecycle.phase is Phase.LOADED


def test_an_older_generation_cannot_finish() -> None:
    lifecycle = WorkspaceLifecycle()
    lifecycle.try_begin_load()
    older = lifecycle.next_generation()
    newer = lifecycle.next_generation()

    older_result = lifecycle.finish(older)
    phase_after_older = lifecycle.phase
    newer_result = lifecycle.finish(newer)

    assert older_result is False
    assert phase_after_older is Phase.LOADING
    assert newer_result is True
    assert lifecycle.phase is Phase.LOADED


@pytest.mark.parametrize(
    ("start", "accepted", "end"),
    [
        (Phase.EMPTY, False, Phase.EMPTY),
        (Phase.LOADING, False, Phase.LOADING),
        (Phase.LOADED, False, Phase.LOADED),
        (Phase.RELOADING, True, Phase.LOADED),
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


@pytest.mark.parametrize("start", [Phase.EMPTY, Phase.LOADED, Phase.FAILED, Phase.CLOSING, Phase.CLOSED])
def test_finish_outside_a_load_changes_nothing(start: Phase) -> None:
    lifecycle = _lifecycle_in(start)
    generation = lifecycle.next_generation()

    result = lifecycle.finish(generation)

    assert result is False
    assert lifecycle.phase is start


@pytest.mark.parametrize("start", [Phase.EMPTY, Phase.LOADING, Phase.LOADED, Phase.RELOADING, Phase.FAILED])
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
        (Phase.LOADED, False),
        (Phase.RELOADING, False),
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
