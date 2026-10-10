from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.api.info import Context, register_on_workspace_destroy
from vsview.app.workspace.base import BaseWorkspace
from vsview.app.workspace.loader import VSEngineWorkspace
from vsview.assets import IconName
from vsview.vsenv import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def test_base_workspace_register_and_run_callbacks(qtbot: QtBot) -> None:
    workspace = BaseWorkspace()
    qtbot.addWidget(workspace)

    order = list[int]()

    def cb1() -> None:
        order.append(1)

    def cb2() -> None:
        order.append(2)

    workspace.register_on_destroy(cb1)
    workspace.register_on_destroy(cb2)

    workspace.deleteLater()

    # LIFO order
    assert order == [2, 1]


def test_base_workspace_register_with_args(qtbot: QtBot) -> None:
    workspace = BaseWorkspace()
    qtbot.addWidget(workspace)

    results = list[str]()

    def cb(name: str, *, prefix: str = "") -> None:
        results.append(f"{prefix}{name}")

    workspace.register_on_destroy(cb, "test", prefix="pre_")
    workspace.deleteLater()

    assert results == ["pre_test"]


def test_base_workspace_unregister(qtbot: QtBot) -> None:
    workspace = BaseWorkspace()
    qtbot.addWidget(workspace)

    called = list[str]()

    def cb1() -> None:
        called.append("cb1")

    def cb2() -> None:
        called.append("cb2")

    workspace.register_on_destroy(cb1)
    workspace.register_on_destroy(cb2)
    workspace.unregister_on_destroy(cb1)

    workspace.deleteLater()

    assert called == ["cb2"]


def test_base_workspace_callback_error_resilience(qtbot: QtBot) -> None:
    workspace = BaseWorkspace()
    qtbot.addWidget(workspace)

    called = list[str]()

    def failing_cb() -> None:
        raise RuntimeError("Cleanup failure")

    def passing_cb() -> None:
        called.append("passed")

    workspace.register_on_destroy(passing_cb)
    workspace.register_on_destroy(failing_cb)

    # Should not raise exception
    workspace.deleteLater()

    assert called == ["passed"]


def test_on_workspace_destroy_inside_preview(mocker: MockerFixture, qtbot: QtBot) -> None:
    workspace = BaseWorkspace()
    qtbot.addWidget(workspace)

    ctx = Context(
        reload_count=0,
        is_reload=False,
        persistent_state={},
        on_workspace_destroy=workspace.register_on_destroy,
    )
    mod = ModuleType("__vsview__")
    mod.__vsview_context__ = ctx  # type: ignore[attr-defined]

    mocker.patch.dict(sys.modules, {"__vsview__": mod})

    called = False

    def cleanup() -> None:
        nonlocal called
        called = True

    returned_cb = register_on_workspace_destroy(cleanup)
    assert returned_cb is cleanup

    workspace.deleteLater()
    assert called is True


def test_vsengine_workspace_state_available_during_destroy(qtbot: QtBot, qt_event_loop: QtEventLoop) -> None:
    class DummyWorkspace(VSEngineWorkspace[str]):
        title = "Dummy"
        icon = IconName.CODE
        content_type = "code"

    workspace = DummyWorkspace()
    qtbot.addWidget(workspace)
    workspace.persistent_state["test_key"] = "test_value"

    read_state = dict[str, Any]()

    def cleanup() -> None:
        read_state["test_key"] = workspace.persistent_state.get("test_key")

    workspace.register_on_destroy(cleanup)
    workspace.deleteLater()

    # State was still intact during cleanup callback execution
    assert read_state["test_key"] == "test_value"
    # State cleared afterwards
    assert workspace.persistent_state == {}
