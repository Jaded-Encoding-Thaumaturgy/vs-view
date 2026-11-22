from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject

from tests.harness import MockQObject
from vsview.app.plugins._controller import PluginWorkspaceController
from vsview.app.plugins.api import PluginAPI
from vsview.app.plugins.exceptions import BlockerRefusedError
from vsview.app.workspace.lifecycle import WorkspaceLifecycle

pytestmark = [pytest.mark.unit, pytest.mark.qt]


@pytest.fixture
def lifecycle() -> WorkspaceLifecycle:
    return WorkspaceLifecycle()


@pytest.fixture
def workspace(mock_qobject: MockQObject, lifecycle: WorkspaceLifecycle) -> MockQObject:
    mock_qobject.lifecycle = lifecycle
    return mock_qobject


@pytest.fixture
def api(workspace: MockQObject) -> PluginAPI:
    return PluginWorkspaceController(workspace).api  # pyright: ignore[reportArgumentType]


def test_workspace_blocker_acquire_release(api: PluginAPI) -> None:
    caller = QObject()
    blocker = api.blocker(caller)  # pyright: ignore[reportArgumentType]

    assert api.busy is False

    # Acquire
    assert blocker.acquire() is True
    assert blocker.locked is True
    assert api.busy is True

    # Release
    blocker.release()
    assert blocker.locked is False
    assert api.busy is False


def test_workspace_blocker_acquire_nonblocking(api: PluginAPI) -> None:
    blocker = api.blocker()

    assert blocker.acquire() is True
    assert blocker.locked is True
    assert api.busy is True

    # Non-blocking acquire on already acquired lock should fail
    assert blocker.acquire(block=False) is False

    blocker.release()
    assert blocker.locked is False
    assert api.busy is False

    # Acquire again should succeed
    assert blocker.acquire(block=False) is True
    assert blocker.locked is True
    assert api.busy is True
    blocker.release()


def test_workspace_blocker_context_manager(api: PluginAPI) -> None:
    caller = QObject()
    blocker = api.blocker(caller)  # pyright: ignore[reportArgumentType]

    assert api.busy is False

    with blocker:
        assert blocker.locked is True
        assert api.busy is True

    assert blocker.locked is False
    assert api.busy is False


def test_workspace_blocker_threads(api: PluginAPI) -> None:
    blocker = api.blocker()

    # Acquire in main thread
    assert blocker.acquire() is True
    assert blocker.locked is True

    # Attempt to acquire in background thread with timeout
    results = list[bool]()

    def worker() -> None:
        success = blocker.acquire(block=True, timeout=0.1)
        results.append(success)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert results == [False]

    # Release main thread
    blocker.release()

    # Now background thread should be able to acquire
    t2 = threading.Thread(target=worker)
    t2.start()
    t2.join()
    assert results == [False, True]


@pytest.mark.parametrize(
    "replace_or_close",
    [
        pytest.param(WorkspaceLifecycle.try_begin_load, id="loading"),
        pytest.param(WorkspaceLifecycle.begin_close, id="closing"),
    ],
)
def test_workspace_blocker_is_refused_while_the_workspace_is_replaced_or_closing(
    api: PluginAPI, lifecycle: WorkspaceLifecycle, replace_or_close: Callable[[WorkspaceLifecycle], object]
) -> None:
    blocker = api.blocker()
    replace_or_close(lifecycle)

    acquired = blocker.acquire(block=False)

    assert acquired is False
    assert blocker.locked is False
    assert api.busy is False
    with pytest.raises(BlockerRefusedError, match="closing"), blocker:
        pass
