from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import SIGNAL, Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from tests.scripts import dual_output_script, script_source
from vsview.app.plugins.manager import PluginManager
from vsview.app.workspace.loader import VSEngineWorkspace
from vsview.assets import IconName
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]

TAB_CHANGED = SIGNAL("tabChanged(int)")

DUAL_OUTPUT_SCRIPT = script_source(dual_output_script, length=10)


class ScriptWorkspace(VSEngineWorkspace[Path]):
    title = "Test Script"
    icon = IconName.FILE_TEXT
    content_type = "script"


@pytest.fixture
def workspace(
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
    init_plugin_manager: PluginManager,
    tmp_path: Path,
) -> ScriptWorkspace:
    script_file = tmp_path / "dual_output.vpy"
    script_file.write_text(DUAL_OUTPUT_SCRIPT, encoding="utf-8")

    ws = ScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    return ws


def _switch_tab(qtbot: QtBot, ws: ScriptWorkspace, index: int) -> None:
    ws.tab_manager.switch_tab(index)
    qtbot.waitUntil(
        lambda: ws.outputs_manager.current_video_index == index and ws.playback.is_idle,
        timeout=3000,
    )


def test_set_cursor_keeps_one_tab_changed_receiver(workspace: ScriptWorkspace) -> None:
    receivers_before = workspace.tab_manager.receivers(TAB_CHANGED)

    for _ in range(50):
        workspace.plugin_controller.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)

    assert workspace.tab_manager.receivers(TAB_CHANGED) == receivers_before + 1


def test_tab_change_resets_the_viewport_cursor(qtbot: QtBot, workspace: ScriptWorkspace) -> None:
    viewport = workspace.tab_manager.tabs.view(0).viewport()
    workspace.plugin_controller.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)
    assert viewport.cursor().shape() == Qt.CursorShape.CrossCursor

    _switch_tab(qtbot, workspace, 1)

    assert viewport.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_tab_change_resets_again_after_a_new_set_cursor(qtbot: QtBot, workspace: ScriptWorkspace) -> None:
    viewport = workspace.tab_manager.tabs.view(0).viewport()
    workspace.plugin_controller.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)
    _switch_tab(qtbot, workspace, 1)
    _switch_tab(qtbot, workspace, 0)
    workspace.plugin_controller.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)

    _switch_tab(qtbot, workspace, 1)

    assert viewport.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_tab_change_after_the_viewport_is_destroyed_does_not_raise(qtbot: QtBot, workspace: ScriptWorkspace) -> None:
    view = workspace.tab_manager.tabs.view(0)
    workspace.plugin_controller.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)
    # QAbstractScrollArea.setViewport deletes the previous viewport widget.
    view.setViewport(QWidget())

    with qtbot.captureExceptions() as exceptions:
        _switch_tab(qtbot, workspace, 1)

    assert exceptions == []
