from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QObject

from tests.harness import MockQObject
from vsview.app.plugins._controller import PluginWorkspaceController
from vsview.app.plugins.api import PluginAPI, WorkspaceBlocker

pytestmark = [pytest.mark.unit, pytest.mark.qt]


@pytest.fixture
def api(mock_qobject: MockQObject) -> PluginAPI:
    controller = PluginWorkspaceController(mock_qobject)  # pyright: ignore[reportArgumentType]
    return controller.api


def test_workspace_blocker_init(api: PluginAPI) -> None:
    caller = QObject()

    # Create blocker with caller
    blocker = api.blocker(caller)  # pyright: ignore[reportArgumentType]
    assert isinstance(blocker, WorkspaceBlocker)
    assert blocker.locked is False

    # Create blocker without caller
    blocker2 = api.blocker()
    assert isinstance(blocker2, WorkspaceBlocker)
    assert blocker2.locked is False


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
