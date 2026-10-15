from __future__ import annotations

import linecache
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any, override

import pytest
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.api._helpers import output_metadata
from vsview.api.info import Context, register_on_workspace_destroy
from vsview.app.workspace.base import BaseWorkspace
from vsview.app.workspace.loader import VSEngineWorkspace
from vsview.app.workspace.quick_script import QuickScriptWorkspace
from vsview.app.workspace.utils import CodeContent
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


def test_quick_script_workspace_delete_later_cleans_caches(
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
) -> None:
    workspace = QuickScriptWorkspace()
    qtbot.addWidget(workspace)

    filename = workspace.filename
    linecache.cache[filename] = (100, None, ["line1\n", "line2\n"], filename)
    output_metadata[filename] = {0: "test_output_meta"}

    assert filename in linecache.cache
    assert filename in output_metadata

    workspace.deleteLater()

    assert filename not in linecache.cache
    assert filename not in output_metadata


@pytest.mark.vpy("no-core")
def test_quick_script_workspace_loader_registers_source_length(
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> None:
    class TestQuickScriptWorkspace(QuickScriptWorkspace):
        @property
        @override
        def env(self) -> ManagedEnvironment:
            if not self._env or self._env.disposed:
                self._env = vpy_env_factory()
            return self._env

    workspace = TestQuickScriptWorkspace()
    qtbot.addWidget(workspace)

    test_code = (
        "import vapoursynth as vs\n"
        "core = vs.core\n"
        "clip = core.std.BlankClip(length=5, width=64, height=64, format=vs.RGB24)\n"
        "clip.set_output(0)\n"
    )
    workspace.content = CodeContent(test_code, workspace.filename)

    linecache.cache.pop(workspace.filename, None)
    try:
        with workspace.env.use():
            workspace.loader()

        assert workspace.filename in linecache.cache
        entry = linecache.cache[workspace.filename]
        assert len(entry) == 4
        assert isinstance(entry[0], int)
        assert entry[0] == len(workspace.content.code)
        assert entry[0] == len(test_code)
        assert entry[3] == workspace.filename
    finally:
        linecache.cache.pop(workspace.filename, None)
