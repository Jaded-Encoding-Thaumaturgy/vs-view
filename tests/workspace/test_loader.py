from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest
from PySide6.QtWidgets import QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.manager import PluginManager
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

    def __init__(
        self,
        env_factory: Callable[[], ManagedEnvironment],
        parent: QWidget | None = None,
    ) -> None:
        self._env_factory = env_factory
        super().__init__(parent)

    @property
    @override
    def env(self) -> ManagedEnvironment:
        if not self._env or self._env.disposed:
            self._env = self._env_factory()
        return self._env


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


def _make_valid_script_content(length: int = 100, color: tuple[int, int, int] = (255, 0, 0)) -> str:
    return (
        "import vapoursynth as vs\n"
        "core = vs.core\n"
        f"clip = core.std.BlankClip(length={length}, width=64, height=64, "
        f"format=vs.RGB24, fpsnum=24, fpsden=1, color={list(color)})\n"
        "clip = core.std.SetFrameProps(\n"
        "    clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709\n"
        ")\n"
        "clip.set_output(0)\n"
    )


def _make_dual_output_script_content(length: int = 100) -> str:
    return (
        "import vapoursynth as vs\n"
        "core = vs.core\n"
        f"clip0 = core.std.BlankClip(length={length}, width=64, height=64, "
        "format=vs.RGB24, fpsnum=24, fpsden=1, color=[255, 0, 0])\n"
        "clip0 = core.std.SetFrameProps(\n"
        "    clip0, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709\n"
        ")\n"
        f"clip1 = core.std.BlankClip(length={length}, width=64, height=64, "
        "format=vs.RGB24, fpsnum=24, fpsden=1, color=[0, 255, 0])\n"
        "clip1 = core.std.SetFrameProps(\n"
        "    clip1, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709\n"
        ")\n"
        "clip0.set_output(0)\n"
        "clip1.set_output(1)\n"
    )


# Page Transition and Cursor Calculation Tests
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
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    script_file = tmp_path / "cursor_test.vpy"
    script_file.write_text(_make_valid_script_content(length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
) -> None:
    script_file = tmp_path / "target_frame.vpy"
    script_file.write_text(_make_valid_script_content(length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    voutput.last_frame = 45

    # UNLINK: returns voutput.last_frame
    assert ws._calculate_target_frame(PlayHeadToolButton.State.UNLINK) == 45

    # LINK_FRAME: returns clamped playback state current frame
    ws.playback.state.current_frame = 25
    assert ws._calculate_target_frame(PlayHeadToolButton.State.LINK_FRAME) == 25

    # LINK_FRAME: clamps to upper boundary (num_frames - 1 = 99)
    ws.playback.state.current_frame = 200
    assert ws._calculate_target_frame(PlayHeadToolButton.State.LINK_FRAME) == 99

    # LINK_TIME: maps playback current_time to frame, clamped to valid range
    ws.playback.state.current_time = Time(seconds=1.0)
    expected_frame = voutput.time_to_frame(Time(seconds=1.0))
    assert ws._calculate_target_frame(PlayHeadToolButton.State.LINK_TIME) == expected_frame

    # LINK_ADAPT: resolves dynamically based on timeline mode
    ws.playback.state.current_frame = 30
    ws.tbar.timeline.mode = "frame"
    assert ws._calculate_target_frame(PlayHeadToolButton.State.LINK_ADAPT) == 30


# Script Compilation and Loading Error Tests
def test_script_loading_syntax_error(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_syntax.vpy"
    script_file.write_text("def broken_code(: invalid", encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page

    assert mock_dialog.called
    dialog_msg = mock_dialog.call_args[0][1]
    assert "SyntaxError" in dialog_msg
    assert errored_signals == ["Execution error", "Error while loading content"]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


def test_script_loading_name_error(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_name.vpy"
    script_file.write_text(
        "import vapoursynth as vs\ncore = vs.core\nclip = undefined_variable.BlankClip()\nclip.set_output(0)\n",
        encoding="utf-8",
    )

    ws = ConcreteScriptWorkspace(vpy_env_factory)
    qtbot.addWidget(ws)

    errored_signals = list[str]()
    ws.statusLoadingErrored.connect(errored_signals.append)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.error_page, timeout=3000)

    assert fut.result() == 1
    assert ws.stack.currentWidget() is ws.error_page

    assert mock_dialog.called
    dialog_msg = mock_dialog.call_args[0][1]
    assert "NameError" in dialog_msg
    assert "undefined_variable" in dialog_msg
    assert errored_signals == ["Execution error", "Error while loading content"]

    # Workspace does not crash and remains responsive
    assert ws.isEnabled()
    assert ws.reload_btn.isEnabled()


def test_script_loading_module_not_found_error(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "broken_module.vpy"
    script_file.write_text(
        "import definitely_non_existent_module_404_xyz\n",
        encoding="utf-8",
    )

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "valid.vpy"
    script_file.write_text(_make_valid_script_content(length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "dual_output.vpy"
    script_file.write_text(_make_dual_output_script_content(length=100), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
            and ws.playback.can_reload
        ),
        timeout=3000,
    )

    # Seek to frame 42 and wait for frame render to settle
    ws.playback.request_frame(42)
    qtbot.waitUntil(
        lambda: ws.playback.state.current_frame == 42 and ws.playback.can_reload,
        timeout=3000,
    )
    assert ws.tbar.playback_container.frame_edit.value() == 42

    # Modify script file content
    script_file.write_text(_make_dual_output_script_content(length=120), encoding="utf-8")

    # Trigger hot reload
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.can_reload, timeout=3000)
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
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "time_sync.vpy"
    script_file.write_text(_make_valid_script_content(length=120), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    script_file.write_text(_make_valid_script_content(length=150), encoding="utf-8")

    # Reload
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.can_reload, timeout=3000)
    assert fut_reload.result() == 0

    # Assert timeline mode and frame position preserved
    assert ws.tbar.timeline.mode == "time"
    assert ws.playback.state.current_frame == 48
    assert ws.tbar.playback_container.frame_edit.value() == 48
    assert ws.stack.currentWidget() is ws.loaded_page


def test_reload_content_rejected_when_busy(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
) -> None:
    script_file = tmp_path / "busy_check.vpy"
    script_file.write_text(_make_valid_script_content(length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
    qtbot.addWidget(ws)

    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=3000)
    assert fut.result() == 0

    # 1. When playback cannot reload (busy)
    ws.playback.can_reload = False
    fut_reject1 = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reject1.done(), timeout=2000)
    assert fut_reject1.result() == 2
    assert ws.reload_count == 0

    ws.playback.can_reload = True

    # 2. When plugin API is busy (using workspace blocker)
    with ws.api.blocker():
        assert ws.api.busy is True
        fut_reject2 = ws.reload_content()
        qtbot.waitUntil(lambda: fut_reject2.done(), timeout=2000)
        assert fut_reject2.result() == 2
        assert ws.reload_count == 0

    assert ws.api.busy is False


# Error Recovery Lifecycle Tests
def test_error_recovery_lifecycle_syntax_error(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "lifecycle_syntax.vpy"
    script_file.write_text(_make_valid_script_content(length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    script_file.write_text(_make_valid_script_content(length=80), encoding="utf-8")

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
    assert ws.playback.can_reload is True


def test_error_recovery_lifecycle_name_error(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mock_dialog = mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "lifecycle_name.vpy"
    script_file.write_text(_make_valid_script_content(length=60), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
    script_file.write_text(_make_valid_script_content(length=90), encoding="utf-8")
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
    assert ws.playback.can_reload is True


def test_repeated_error_recovery_cycles(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    tmp_path: Path,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> None:
    mocker.patch("vsview.app.error.display_error_dialog", return_value=0)
    script_file = tmp_path / "repeated_cycles.vpy"
    script_file.write_text(_make_valid_script_content(length=50), encoding="utf-8")

    ws = ConcreteScriptWorkspace(vpy_env_factory)
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
        script_file.write_text(_make_valid_script_content(length=50 + (cycle + 1) * 10), encoding="utf-8")
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
