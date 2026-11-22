from __future__ import annotations

import threading
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any, override

import pytest
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken
from vsengine.loops import Cancelled

from tests.scripts import audio_tracks_script, blank_clip_script, dual_output_script, script_source
from vsview.api import hookimpl
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.api import WidgetPluginBase
from vsview.app.plugins.exceptions import NoEnvironmentError
from vsview.app.plugins.manager import PluginManager
from vsview.app.views import PluginDock
from vsview.app.views.video import GraphicsView
from vsview.app.workspace.lifecycle import Phase
from vsview.app.workspace.loader import LoaderWorkspace, VSEngineWorkspace
from vsview.app.workspace.tab_manager import PlayHeadToolButton
from vsview.assets import IconName
from vsview.types import Frame, Time
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


class ConcreteScriptWorkspace(VSEngineWorkspace[Path]):
    title = "Test Script"
    icon = IconName.FILE_TEXT
    content_type = "script"


class ConcreteLoaderWorkspace(LoaderWorkspace[str]):
    title = "Test Workspace"
    icon = IconName.CODE
    content_type = "code"

    def loader(self) -> None: ...


@pytest.fixture
def loader_workspace(qt_event_loop: QtEventLoop, qtbot: QtBot) -> ConcreteLoaderWorkspace:
    ws = ConcreteLoaderWorkspace()
    qtbot.addWidget(ws)
    return ws


@pytest.fixture(autouse=True)
def setup_plugin_manager(init_plugin_manager: PluginManager) -> PluginManager:
    return init_plugin_manager


@pytest.fixture(autouse=True)
def setup_qt_event_loop(qt_event_loop: QtEventLoop) -> QtEventLoop:
    return qt_event_loop


# Page Transition and Cursor Calculation Tests
@pytest.mark.vpy("no-core")
def test_loader_workspace_page_transitions(loader_workspace: ConcreteLoaderWorkspace) -> None:
    ws = loader_workspace

    # Initial state should be empty page
    assert ws.stack.currentWidget() is ws.empty_page

    # Transition to loading page
    ws.set_loading_page()
    assert ws.stack.currentWidget() is ws.loading_page

    # Transition to error page
    ws.set_error_page()
    assert ws.stack.currentWidget() is ws.error_page

    # Transition back to empty page
    ws.set_empty_page()
    assert ws.stack.currentWidget() is ws.empty_page

    # Transition to loaded page
    ws.set_loaded_page()
    assert ws.stack.currentWidget() is ws.loaded_page


def test_loader_update_timeline_cursor(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    script_file = tmp_path / "cursor_test.vpy"
    script_file.write_text(script_source(blank_clip_script, length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)

    # 1. Guard check: when a frame seek request is pending, cursor must NOT update
    mocker.patch.object(type(ws.playback), "has_pending_frame", new_callable=mocker.PropertyMock, return_value=True)
    assert ws.playback.has_pending_frame is True
    ws.update_timeline_cursor(42)
    assert ws.tbar.playback_container.frame_edit.value() == 0

    # 2. When has_pending_frame is False, timeline cursor and edit widgets must update
    mocker.patch.object(type(ws.playback), "has_pending_frame", new_callable=mocker.PropertyMock, return_value=False)
    assert ws.playback.has_pending_frame is False
    ws.update_timeline_cursor(42)
    assert ws.tbar.playback_container.frame_edit.value() == 42
    assert ws.tbar.playback_container.time_edit.time() == voutput.frame_to_time(Frame(42)).to_qtime()


def test_loader_calculate_target_frame(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "target_frame.vpy"
    script_file.write_text(script_source(blank_clip_script, length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    voutput.last_frame = 45

    # UNLINK: returns voutput.last_frame
    assert ws.calculate_target_frame(PlayHeadToolButton.State.UNLINK) == 45

    # LINK_FRAME: returns clamped playback state current frame
    ws.playback.state.current_frame = 25
    assert ws.calculate_target_frame(PlayHeadToolButton.State.LINK_FRAME) == 25

    # LINK_FRAME: clamps to upper boundary (num_frames - 1 = 99)
    ws.playback.state.current_frame = 200
    assert ws.calculate_target_frame(PlayHeadToolButton.State.LINK_FRAME) == 99

    # LINK_TIME: maps playback current_time to frame, clamped to valid range
    ws.playback.state.current_time = Time(seconds=1.0)
    expected_frame = voutput.time_to_frame(Time(seconds=1.0))
    assert ws.calculate_target_frame(PlayHeadToolButton.State.LINK_TIME) == expected_frame

    # LINK_ADAPT: resolves dynamically based on timeline mode
    ws.playback.state.current_frame = 30
    ws.tbar.timeline.mode = "frame"
    assert ws.calculate_target_frame(PlayHeadToolButton.State.LINK_ADAPT) == 30


# Script Compilation and Loading Error Tests
def test_script_loading_syntax_error(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_syntax.vpy"
    script_file.write_text("def broken_code(: invalid", encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page

    assert mock_dialog.called
    dialog_msg = mock_dialog.call_args[0][1]
    assert dialog_msg.startswith("A SyntaxError exception was raised while loading the script.\n")
    assert errored_signals == ["Syntax Error", "Error while loading content"]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


def test_script_loading_name_error(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_name.vpy"
    script_file.write_text(
        "import vapoursynth as vs\ncore = vs.core\nclip = undefined_variable.BlankClip()\nclip.set_output(0)\n",
        encoding="utf-8",
    )

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page

    assert mock_dialog.called
    dialog_msg = mock_dialog.call_args[0][1]
    assert dialog_msg.startswith("A NameError exception was raised while running the script.\n")
    assert "undefined_variable" in dialog_msg
    assert errored_signals == ["Execution error", "Error while loading content"]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


def test_script_loading_module_not_found_error(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_module.vpy"
    script_file.write_text(
        "import definitely_non_existent_module_404_xyz\n",
        encoding="utf-8",
    )

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page

    assert mock_dialog.called
    dialog_msg = mock_dialog.call_args[0][1]
    assert "ModuleNotFoundError" in dialog_msg
    assert "definitely_non_existent_module_404_xyz" in dialog_msg
    assert errored_signals == ["Execution error", "Error while loading content"]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


@pytest.mark.vpy("no-core")
def test_concrete_loader_workspace_loader_error(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    class FailingLoaderWorkspace(ConcreteLoaderWorkspace):
        def loader(self) -> None:
            raise RuntimeError("Direct loader failure")

    ws = FailingLoaderWorkspace()
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content("dummy_content")
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page
    assert errored_signals == ["Error while loading content"]
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


# Hot Reloading and State Synchronization Tests
def test_script_loading_valid(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "valid.vpy"
    script_file.write_text(script_source(blank_clip_script, length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)

    assert fut.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1
    assert ws.outputs_manager.current_video_index == 0
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_index == 0
    assert ws.tbar.playback_container.frame_edit.value() == 0


def test_hot_reload_state_synchronization(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "dual_output.vpy"
    script_file.write_text(script_source(dual_output_script, length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    # Initial load
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0
    assert len(ws.outputs_manager.voutputs) == 2
    assert ws.outputs_manager.current_video_index == 0

    # Switch tab to index 1 and wait for it to fully load
    ws.tab_manager.switch_tab(1)
    qtbot.waitUntil(
        lambda: (
            ws.outputs_manager.current_video_index == 1
            and ws.outputs_manager.voutputs[1].loaded_once
            and ws.playback.is_idle
        ),
        timeout=3000,
    )

    # Seek to frame 42 and wait for frame render to settle
    ws.playback.request_frame(42)
    qtbot.waitUntil(
        lambda: ws.playback.state.current_frame == 42 and ws.playback.is_idle,
        timeout=3000,
    )
    assert ws.tbar.playback_container.frame_edit.value() == 42

    # Modify script file content
    script_file.write_text(script_source(dual_output_script, length=120), encoding="utf-8")

    # Trigger hot reload
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.is_idle, timeout=3000)
    assert fut_reload.result() == 0

    # Verify preserved state across successful reload
    assert ws.reload_count == 1
    assert ws.stack.currentWidget() is ws.loaded_page
    assert ws.outputs_manager.current_video_index == 1
    assert ws.playback.state.current_frame == 42
    assert ws.tbar.playback_container.frame_edit.value() == 42
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 120


def test_hot_reload_time_mode_synchronization(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "time_sync.vpy"
    script_file.write_text(script_source(blank_clip_script, length=120), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    # Configure timeline mode to "time" and playhead sync to LINK_TIME
    ws.tbar.timeline.mode = "time"
    ws.tab_manager.sync_playhead_btn.set_state(state=PlayHeadToolButton.State.LINK_TIME)

    # Seek to frame 48 (which corresponds to 2.0s at 24fps)
    ws.playback.request_frame(48)
    qtbot.waitUntil(lambda: ws.playback.state.current_frame == 48, timeout=2000)

    # Modify script content (increase length to 150)
    script_file.write_text(script_source(blank_clip_script, length=150), encoding="utf-8")

    # Reload
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.is_idle, timeout=3000)
    assert fut_reload.result() == 0

    # Assert timeline mode and frame position preserved
    assert ws.tbar.timeline.mode == "time"
    assert ws.playback.state.current_frame == 48
    assert ws.tbar.playback_container.frame_edit.value() == 48
    assert ws.stack.currentWidget() is ws.loaded_page


def test_reload_content_rejected_when_busy(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "busy_check.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    with ws.plugin_controller.api.blocker():
        assert ws.plugin_controller.busy is True
        fut_reject2 = ws.reload_content()
        qtbot.waitUntil(lambda: fut_reject2.done(), timeout=2000)
        assert fut_reject2.result() == 2
        assert ws.reload_count == 0

    assert ws.plugin_controller.busy is False


# Lifecycle Phase Tests
@pytest.mark.parametrize(
    ("script_content", "expected_result", "expected_phase"),
    [
        pytest.param(script_source(blank_clip_script, length=50), 0, Phase.LOADED, id="valid"),
        pytest.param("broken = [invalid syntax", 1, Phase.FAILED, id="broken"),
    ],
)
def test_load_ends_in_the_phase_matching_its_result(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
    script_content: str,
    expected_result: int,
    expected_phase: Phase,
) -> None:
    mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "phase.vpy"
    script_file.write_text(script_content, encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is expected_phase, timeout=3000)

    assert fut.result() == expected_result
    assert ws.lifecycle.phase is expected_phase


def test_reload_of_a_failed_load_is_refused(
    qtbot: QtBot,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "failed.vpy"
    script_file.write_text("broken = [invalid syntax", encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.FAILED, timeout=3000)

    fut_reload = ws.reload_content()
    qtbot.waitUntil(fut_reload.done, timeout=2000)

    assert fut_reload.result() == 2
    assert ws.reload_count == 0
    assert ws.lifecycle.phase is Phase.FAILED
    assert ws.stack.currentWidget() is ws.error_page


@pytest.mark.vpy("no-core")
def test_a_load_requested_after_close_starts_nothing(loader_workspace: ConcreteLoaderWorkspace) -> None:
    loader_workspace.deleteLater()

    fut = loader_workspace.load_content("dummy_content")

    assert fut.result(timeout=2) == 2
    assert loader_workspace.lifecycle.phase is Phase.CLOSED
    assert loader_workspace.stack.currentWidget() is loader_workspace.empty_page


# Environment and Release Tests
@pytest.mark.vpy("no-core")
@pytest.mark.parametrize("close_first", [False, True], ids=["nothing_loaded", "closed"])
def test_vs_context_without_an_environment_raises(loader_workspace: ConcreteLoaderWorkspace, close_first: bool) -> None:
    if close_first:
        loader_workspace.deleteLater()

    with (
        pytest.raises(NoEnvironmentError, match=r"VapourSynth environment"),
        loader_workspace.plugin_controller.api.vs_context(),
    ):
        pass


def _register_unload_and_destroy_callbacks(*, record: Callable[[str], None]) -> None:
    import vsview

    vsview.register_on_unload(record, "unload")
    vsview.register_on_workspace_destroy(record, "destroy")


def test_script_unload_callbacks_run_at_each_release_and_destroy_callbacks_once_at_close(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    events = list[str]()
    script_file = tmp_path / "callbacks.vpy"
    script_file.write_text(
        script_source(blank_clip_script, length=10) + script_source(_register_unload_and_destroy_callbacks),
        encoding="utf-8",
    )
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    ws.vsargs = {"record": events.append}
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)

    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    events_after_reload = list(events)
    ws.teardown()

    assert events_after_reload == ["unload"]
    assert events == ["unload", "unload", "destroy"]


def test_a_removed_release_callback_does_not_run_at_reload(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "release.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    calls = list[str]()
    handle = ws.plugin_controller.api.register_on_destroy(lambda: calls.append("removed"))
    ws.plugin_controller.api.register_on_destroy(lambda: calls.append("kept"))

    handle.remove()
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)

    assert calls == ["kept"]


class _ShortTaskTimeoutScriptWorkspace(ConcreteScriptWorkspace):
    task_timeout_ms = 100


def register_unload_callback(message: str, *, record: Callable[[str], None]) -> None:
    import vsview

    vsview.register_on_unload(record, message)


def test_closing_during_a_running_script_disposes_its_environment_after_the_script_ends(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    started, gate = threading.Event(), threading.Event()
    events = list[str]()
    script_file = tmp_path / "slow.vpy"
    script_file.write_text(
        script_source(wait_for_gate)
        + script_source(register_unload_callback, message="registered after the release")
        + script_source(blank_clip_script, length=10),
        encoding="utf-8",
    )
    ws = _ShortTaskTimeoutScriptWorkspace()
    qtbot.addWidget(ws)
    ws.vsargs = {"started": started, "gate": gate, "record": events.append}
    ws.load_content(script_file)
    qtbot.waitUntil(started.is_set, timeout=5000)
    env = ws.env

    disposal = ws.teardown()
    disposed_while_running = env.disposed
    gate.set()
    qtbot.waitUntil(lambda: disposal is not None and disposal.done(), timeout=5000)

    assert disposed_while_running is False
    assert env.disposed is True
    assert events == ["registered after the release"]


@pytest.mark.vpy("no-core")
def test_unload_callbacks_are_dropped_when_there_is_no_environment(loader_workspace: ConcreteLoaderWorkspace) -> None:
    calls = list[str]()
    loader_workspace.register_on_unload(calls.append, "unload")
    loader_workspace.release_environment()
    loader_workspace.teardown()

    assert calls == []


# Load Pipeline Tests
def test_loading_new_content_into_a_loaded_workspace_releases_the_old_run(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    events = list[str]()
    first = tmp_path / "first.vpy"
    first.write_text(
        script_source(blank_clip_script, length=10) + script_source(register_unload_callback, message="first unloaded"),
        encoding="utf-8",
    )
    second = tmp_path / "second.vpy"
    second.write_text(script_source(blank_clip_script, length=20), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    ws.vsargs = {"record": events.append}
    fut = ws.load_content(first)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    first_env = ws.env

    fut_second = ws.load_content(second)
    qtbot.waitUntil(lambda: fut_second.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)

    assert fut_second.result() == 0
    assert events == ["first unloaded"]
    assert first_env.disposed is True
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 20


def test_a_reload_is_refused_while_a_task_of_the_workspace_still_runs(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "refused.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = _ShortTaskTimeoutScriptWorkspace()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    env_before = ws.env
    gate = threading.Event()
    task = ws.loop.to_thread_scoped(ws, "SlowPluginTask", gate.wait, 10)

    fut_refused = ws.reload_content()
    qtbot.waitUntil(fut_refused.done, timeout=5000)
    popups = [
        w
        for w in QApplication.topLevelWidgets()
        if isinstance(w, QMessageBox) and w.isVisible() and w.windowTitle() == "Reload Refused"
    ]
    gate.set()
    task.result(timeout=5)

    assert fut_refused.result() == 2
    assert ws.lifecycle.phase is Phase.LOADED
    assert ws.env is env_before
    assert env_before.disposed is False
    assert ws.reload_count == 0
    assert ws.tbar.isEnabled() is True
    assert ws.content_area.isEnabled() is True
    assert len(popups) == 1
    assert "SlowPluginTask" in popups[0].text()

    for popup in popups:
        popup.close()
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    assert fut_reload.result() == 0
    assert ws.reload_count == 1


def gated_broken_frame_script(broken_frame: int, *, rendering: threading.Event, gate: threading.Event) -> None:
    import vapoursynth as vs

    clip = vs.core.std.BlankClip(length=50, width=64, height=64, format=vs.RGB24, fpsnum=24, fpsden=1)
    clip = vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )

    def _eval(n: int) -> vs.VideoNode:
        if n == broken_frame:
            rendering.set()
            gate.wait(5)
            raise ValueError("broken frame")
        return clip

    vs.core.std.FrameEval(clip, _eval).set_output(0)


def wait_for_gate(*, started: threading.Event, gate: threading.Event) -> None:
    started.set()
    gate.wait(10)


def test_a_frame_that_fails_to_render_during_a_reload_does_not_fail_the_reload(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    rendering, gate = threading.Event(), threading.Event()
    script_file = tmp_path / "broken_frame.vpy"
    script_file.write_text(script_source(gated_broken_frame_script, broken_frame=10), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    ws.vsargs = {"rendering": rendering, "gate": gate}
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    ws.playback.request_frame(10)
    qtbot.waitUntil(rendering.is_set, timeout=3000)

    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: ws.lifecycle.phase is Phase.REPLACING, timeout=3000)
    gate.set()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.lifecycle.phase in (Phase.LOADED, Phase.FAILED), timeout=5000)

    assert fut_reload.result() == 0
    assert ws.lifecycle.phase is Phase.LOADED
    assert ws.reload_count == 1
    assert ws.stack.currentWidget() is ws.loaded_page
    assert ws.tab_manager.tabs.count() == 1


class _CancelledBeforeReleaseWorkspace(ConcreteScriptWorkspace):
    @override
    def on_before_reload(self) -> None:
        raise Cancelled("cancelled before the loaded content is released")


class _CancelledAfterReleaseWorkspace(ConcreteScriptWorkspace):
    @override
    def on_views_created(self, views: Sequence[GraphicsView]) -> None:
        if self.reload_count > 0:
            raise Cancelled("cancelled after the loaded content is released")


@pytest.mark.parametrize(
    ("workspace_type", "expected_phase", "expected_page", "env_disposed"),
    [
        pytest.param(
            _CancelledBeforeReleaseWorkspace,
            Phase.LOADED,
            lambda ws: ws.loaded_page,
            False,
            id="before-release-keeps-the-content",
        ),
        pytest.param(
            _CancelledAfterReleaseWorkspace,
            Phase.FAILED,
            lambda ws: ws.error_page,
            True,
            id="after-release-fails",
        ),
    ],
)
def test_a_cancelled_reload_keeps_the_loaded_content_only_until_its_release(
    qtbot: QtBot,
    tmp_path: Path,
    workspace_type: type[ConcreteScriptWorkspace],
    expected_phase: Phase,
    expected_page: Callable[[ConcreteScriptWorkspace], QWidget],
    env_disposed: bool,
) -> None:
    script_file = tmp_path / "cancelled.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = workspace_type()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
    env_before = ws.env

    fut_reload = ws.reload_content()
    qtbot.waitUntil(fut_reload.done, timeout=5000)

    assert isinstance(fut_reload.exception(), Cancelled)
    assert ws.lifecycle.phase is expected_phase
    assert ws.stack.currentWidget() is expected_page(ws)
    assert env_before.disposed is env_disposed
    assert ws.tbar.isEnabled() is True


def test_a_load_while_another_load_runs_is_refused(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    started, gate = threading.Event(), threading.Event()
    slow = tmp_path / "slow.vpy"
    slow.write_text(script_source(wait_for_gate) + script_source(blank_clip_script, length=10), encoding="utf-8")
    other = tmp_path / "other.vpy"
    other.write_text(script_source(blank_clip_script, length=20), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    ws.vsargs = {"started": started, "gate": gate}
    fut_slow = ws.load_content(slow)
    qtbot.waitUntil(started.is_set, timeout=5000)

    fut_other = ws.load_content(other)
    qtbot.waitUntil(fut_other.done, timeout=5000)
    gate.set()
    qtbot.waitUntil(lambda: fut_slow.done() and ws.lifecycle.phase is Phase.LOADED, timeout=5000)

    assert fut_other.result() == 2
    assert fut_slow.result() == 0
    assert ws.content == slow


def test_a_busy_plugin_does_not_stop_the_render_at_the_end_of_a_load(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "busy.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    with ws.plugin_controller.api.blocker():
        fut = ws.load_content(script_file)
        qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=3000)
        pixmap_is_null = ws.tab_manager.current_view.pixmap_item.pixmap().isNull()

    assert fut.result() == 0
    assert pixmap_is_null is False


class _BrokenViewsScriptWorkspace(ConcreteScriptWorkspace):
    @override
    def on_views_created(self, views: Sequence[GraphicsView]) -> None:
        raise RuntimeError("views broken")


def test_an_error_after_the_script_ran_ends_the_load_as_failed(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "broken_views.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = _BrokenViewsScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.FAILED, timeout=3000)

    with pytest.raises(RuntimeError, match="views broken"):
        fut.result()
    assert ws.stack.currentWidget() is ws.error_page


# Error Recovery Lifecycle Tests
def test_error_recovery_lifecycle_syntax_error(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "lifecycle_syntax.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    # Step 1: Valid script loaded
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1
    assert ws.tab_manager.tabs.count() == 1

    # Step 2: Reload with broken script
    broken_content = "def invalid_syntax(broken:"
    script_file.write_text(broken_content, encoding="utf-8")

    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)
    assert fut_reload.result() == 1

    # Verify transitions to error_page and clean output cleanup
    assert ws.stack.currentWidget() is ws.error_page
    assert len(ws.outputs_manager.voutputs) == 0
    assert ws.tab_manager.tabs.count() == 0
    assert mock_dialog.called
    assert "SyntaxError" in mock_dialog.call_args[0][1]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()

    # Step 3: Fix script and reload via error_page reload button
    script_file.write_text(script_source(blank_clip_script, length=80), encoding="utf-8")

    ws.reload_btn.click()
    qtbot.waitUntil(
        lambda: ws.stack.currentWidget() is ws.loaded_page,
        timeout=3000,
    )

    # Invariants after recovery
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1
    assert ws.tab_manager.tabs.count() == 1
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 80
    assert ws.playback.is_idle is True
    assert ws.tbar.isEnabled() is True
    assert ws.tbar.timeline.is_events_blocked is False


def test_error_recovery_lifecycle_name_error(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "lifecycle_name.vpy"
    script_file.write_text(script_source(blank_clip_script, length=60), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    # Step 1: Valid initial load
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0
    assert len(ws.outputs_manager.voutputs) == 1

    # Step 2: Reload with NameError
    broken_content = "import vapoursynth as vs\ncore = vs.core\nclip = missing_var.BlankClip()\nclip.set_output(0)\n"
    script_file.write_text(broken_content, encoding="utf-8")

    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)
    assert fut_reload.result() == 1
    assert ws.stack.currentWidget() is ws.error_page
    assert len(ws.outputs_manager.voutputs) == 0
    assert ws.tab_manager.tabs.count() == 0
    assert mock_dialog.called
    assert "NameError" in mock_dialog.call_args[0][1]

    # Step 3: Fix script and recover via reload button
    script_file.write_text(script_source(blank_clip_script, length=90), encoding="utf-8")
    ws.reload_btn.click()
    qtbot.waitUntil(
        lambda: ws.stack.currentWidget() is ws.loaded_page,
        timeout=3000,
    )
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 90
    assert ws.playback.is_idle is True


def test_repeated_error_recovery_cycles(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "repeated_cycles.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    # Initial load (Healthy)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page

    for cycle in range(2):
        # Break script (Cycle failure)
        script_file.write_text("broken = [invalid syntax", encoding="utf-8")
        fut_break = ws.reload_content()
        qtbot.waitUntil(lambda f=fut_break: f.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)
        assert ws.stack.currentWidget() is ws.error_page
        assert len(ws.outputs_manager.voutputs) == 0

        # Fix script (Cycle recovery)
        script_file.write_text(script_source(blank_clip_script, length=50 + (cycle + 1) * 10), encoding="utf-8")
        ws.reload_btn.click()
        qtbot.waitUntil(
            lambda: ws.stack.currentWidget() is ws.loaded_page,
            timeout=3000,
        )
        assert ws.stack.currentWidget() is ws.loaded_page
        assert len(ws.outputs_manager.voutputs) == 1
        voutput = ws.outputs_manager.current_voutput
        assert isinstance(voutput, VideoOutput)
        assert voutput.vs_output.clip.num_frames == 50 + (cycle + 1) * 10


def test_reload_content_tbar_lifecycle(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    script_file = tmp_path / "tbar_lifecycle.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    assert ws.tbar.isEnabled() is True
    assert ws.tbar.timeline.is_events_blocked is False

    spy_disable = mocker.spy(ws.tbar, "disable")
    spy_enable = mocker.spy(ws.tbar, "enable")

    # Update script file and trigger hot reload
    script_file.write_text(script_source(blank_clip_script, length=60), encoding="utf-8")
    fut_reload = ws.reload_content()

    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.is_idle, timeout=3000)

    assert fut_reload.result() == 0
    assert spy_disable.call_count == 1
    assert spy_enable.call_count == 1
    assert ws.tbar.isEnabled() is True
    assert ws.tbar.timeline.is_events_blocked is False


def test_reload_content_failure_destroys_freeze_screenshot_and_enables_tbar(
    qtbot: QtBot,
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "reload_failure.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    spy_disable = mocker.spy(ws.tbar, "disable")
    spy_enable = mocker.spy(ws.tbar, "enable")
    spy_destroy_screenshot = mocker.spy(ws, "_destroy_freeze_screenshot")
    spy_clear_failed = mocker.spy(ws, "clear_failed_load")

    # Corrupt script with invalid syntax to force reload failure
    script_file.write_text("invalid python code syntax (((", encoding="utf-8")

    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut_reload.result() == 1
    assert spy_disable.call_count == 1
    assert spy_destroy_screenshot.call_count == 1
    screenshot = spy_destroy_screenshot.call_args[0][0]
    assert not Shiboken.isValid(screenshot)
    assert spy_clear_failed.call_count == 1
    assert ws.stack.currentWidget() is ws.error_page

    # Verify tbar is not left permanently disabled after failed reload
    assert ws.tbar.isEnabled() is True
    assert ws.tbar.timeline.is_events_blocked is False
    assert spy_enable.call_count == 1

    # Fix script and recover via reload button
    script_file.write_text(script_source(blank_clip_script, length=70), encoding="utf-8")
    ws.reload_btn.click()
    qtbot.waitUntil(lambda: ws.stack.currentWidget() is ws.loaded_page and ws.playback.is_idle, timeout=3000)

    assert ws.stack.currentWidget() is ws.loaded_page
    assert ws.tbar.isEnabled() is True
    assert ws.tbar.timeline.is_events_blocked is False


# Plugin Construction and Load Failure Tests
class _GoodDock(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_good_dock"
    display_name = "Good dock"


class _BrokenDock(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_broken_dock"
    display_name = "Broken dock"

    def __init__(self, parent: QWidget, api: Any) -> None:
        super().__init__(parent, api)
        self.setObjectName("broken_dock_widget")
        raise RuntimeError("dock constructor failed")


class _BrokenPanel(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_broken_panel"
    display_name = "Broken panel"

    def __init__(self, parent: QWidget, api: Any) -> None:
        super().__init__(parent, api)
        self.setObjectName("broken_panel_widget")
        raise RuntimeError("panel constructor failed")


class _GoodPanel(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_good_panel"
    display_name = "Good panel"


class _DockPlugin:
    def __init__(self, dock: type[WidgetPluginBase[Any, Any]]) -> None:
        self.dock = dock

    @hookimpl
    def vsview_register_tooldock(self) -> type[WidgetPluginBase[Any, Any]]:
        return self.dock


class _PanelPlugin:
    def __init__(self, panel: type[WidgetPluginBase[Any, Any]]) -> None:
        self.panel = panel

    @hookimpl
    def vsview_register_toolpanel(self) -> type[WidgetPluginBase[Any, Any]]:
        return self.panel


@pytest.fixture
def register_tools(init_plugin_manager: PluginManager) -> Generator[Callable[..., None]]:
    names = list[str]()

    def register(
        docks: Sequence[type[WidgetPluginBase[Any, Any]]] = (),
        panels: Sequence[type[WidgetPluginBase[Any, Any]]] = (),
    ) -> None:
        # pluggy calls the most recently registered implementation first.
        for plugin in [*map(_DockPlugin, reversed(docks)), *map(_PanelPlugin, reversed(panels))]:
            name = f"test_loader_tools_{len(names)}"
            init_plugin_manager.manager.register(plugin, name=name)
            names.append(name)

    try:
        yield register
    finally:
        for name in names:
            init_plugin_manager.manager.unregister(name=name)


def test_load_content_skips_plugins_whose_constructor_raises(
    qtbot: QtBot,
    tmp_path: Path,
    register_tools: Callable[..., None],
) -> None:
    register_tools(docks=[_BrokenDock, _GoodDock], panels=[_BrokenPanel, _GoodPanel])
    script_file = tmp_path / "plugins.vpy"
    script_file.write_text(script_source(blank_clip_script, length=50), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.playback.is_idle, timeout=5000)
    qtbot.waitUntil(lambda: not ws.findChildren(QWidget, QRegularExpression("^broken_")), timeout=2000)

    tabs = ws.plugin_splitter.plugin_tabs
    assert fut.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page
    assert [type(p) for p in ws.plugins] == [_GoodDock, _GoodPanel]
    assert [d.objectName() for d in ws.dock_container.findChildren(PluginDock)] == ["test_loader_good_dock"]
    assert [type(tabs.widget(i)) for i in range(tabs.count())] == [_GoodPanel]
    assert tabs.isTabVisible(0) is True


class _HoldingAtCloseDock(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_holding_at_close_dock"
    display_name = "Holding at close dock"

    @override
    def on_workspace_destroy(self) -> None:
        with self.api.blocker():
            pass


class _RecordingCloseDock(WidgetPluginBase[Any, Any]):
    identifier = "test_loader_recording_close_dock"
    display_name = "Recording close dock"

    def __init__(self, parent: QWidget, api: Any) -> None:
        super().__init__(parent, api)
        self.destroy_calls = 0

    @override
    def on_workspace_destroy(self) -> None:
        self.destroy_calls += 1


def test_a_plugin_refused_the_workspace_at_close_does_not_stop_the_teardown(
    qtbot: QtBot,
    tmp_path: Path,
    register_tools: Callable[..., None],
) -> None:
    register_tools(docks=[_HoldingAtCloseDock, _RecordingCloseDock])
    script_file = tmp_path / "close.vpy"
    script_file.write_text(script_source(blank_clip_script, length=10), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.lifecycle.phase is Phase.LOADED, timeout=5000)
    env = ws.env
    recorder = next(p for p in ws.plugins if isinstance(p, _RecordingCloseDock))

    ws.teardown()

    assert recorder.destroy_calls == 1
    assert ws.lifecycle.phase is Phase.CLOSED
    assert env.disposed is True


class _PanelPluginFailingOnce:
    def __init__(self) -> None:
        self.calls = 0

    @hookimpl
    def vsview_register_toolpanel(self) -> type[WidgetPluginBase[Any, Any]]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("panel registration failed")
        return _GoodPanel


def test_reload_with_fewer_audio_tracks_selects_the_last_remaining_track(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "audio_tracks.vpy"
    script_file.write_text(script_source(audio_tracks_script, num_tracks=2), encoding="utf-8")
    ws = ConcreteScriptWorkspace()
    qtbot.addWidget(ws)
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.playback.is_idle, timeout=5000)
    combo = ws.tbar.playback_container.audio_output_combo
    combo.setCurrentIndex(1)
    assert ws.outputs_manager.current_audio_index == 1

    script_file.write_text(script_source(audio_tracks_script, num_tracks=1), encoding="utf-8")
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.is_idle, timeout=5000)

    assert fut_reload.result() == 0
    assert len(ws.outputs_manager.aoutputs) == 1
    assert ws.outputs_manager.current_audio_index == 0
    assert ws.outputs_manager.current_aoutput is ws.outputs_manager.aoutputs[0]
    assert combo.currentIndex() == 0
