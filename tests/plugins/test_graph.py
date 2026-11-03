from __future__ import annotations

import base64
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import vapoursynth as vs
import vsgraph
from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, Qt
from PySide6.QtGui import QImage, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment, Policy
from vsview_graph.analysis import (
    Severity,
    collect_media_transitions,
    collect_warnings,
    compute_depth,
    describe_media_delta,
    enrich_cytoscape_payload,
    group_media_transitions,
    group_warnings,
)
from vsview_graph.bridge import ExportFormat, GraphBridge, JsToPyCommand, NodeCopyTarget, PyToJsCommand
from vsview_graph.call import build_filter_call, build_node_report, format_python_value
from vsview_graph.plugin import GraphPlugin
from vsview_graph.settings import GraphShortcut
from vsview_graph.web import GraphWebView

from tests.harness import HarnessPluginController
from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.app.settings.manager import SettingsManager
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


# Fixtures & Helpers
@pytest.fixture(autouse=True)
def qt_event_loop(qt_event_loop: QtEventLoop) -> QtEventLoop:
    return qt_event_loop


@pytest.fixture(autouse=True)
def cleanup_webengine() -> Generator[None]:
    try:
        yield
    finally:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


def make_video_output(clip: vs.VideoNode, index: int = 0) -> VideoOutput:
    """Helper to wrap a VapourSynth clip in a VideoOutput with default packer."""
    return VideoOutput(vs.VideoOutputTuple(clip, None, 0), index, Packer())


@pytest.fixture
def graph_env(vpy_policy: Policy) -> Generator[ManagedEnvironment]:
    with vpy_policy.new_environment(vs.CoreCreationFlags.ENABLE_GRAPH_INSPECTION) as env:
        yield env


@pytest.fixture
def populated_harness(
    qtbot: QtBot,
    tmp_path: Path,
    graph_env: ManagedEnvironment,
) -> tuple[HarnessPluginController, GraphPlugin]:
    """Fixture providing a HarnessPluginController and GraphPlugin loaded with a two-node clip."""
    with graph_env.use():
        clip = graph_env.core.std.BlankClip(width=128, height=128, length=20, format=vs.RGB24)
        clip = graph_env.core.std.Crop(clip, left=8, right=8)
        voutput = make_video_output(clip, index=0)

    harness = HarnessPluginController(qtbot, tmp_path, env=graph_env)
    harness.set_current_voutput(voutput)

    plugin = harness.create_plugin(GraphPlugin)
    qtbot.addWidget(plugin)
    plugin.show()

    return harness, plugin


# Command Bridge & Serialization
def test_graph_bridge_dispatch_and_slots(qtbot: QtBot) -> None:
    """Test GraphBridge Command Bus dispatching, lifecycle, and slot handling."""
    bridge = GraphBridge()
    assert not bridge.is_ready

    # Python -> JS command dispatching
    received_commands = list[tuple[str, str]]()
    bridge.dispatchCommandSignal.connect(lambda cmd, payload: received_commands.append((cmd, payload)))

    bridge.dispatch(PyToJsCommand.SET_DATA, {"nodes": [1, 2, 3]})
    assert len(received_commands) == 1
    assert received_commands[0][0] == PyToJsCommand.SET_DATA
    assert json.loads(received_commands[0][1]) == {"nodes": [1, 2, 3]}

    # onWebReady slot and signal
    with qtbot.waitSignal(bridge.webReady, timeout=1000):
        bridge.onWebReady()
    assert bridge.is_ready

    # JS -> Python command forwarding
    received_js_commands = list[tuple[str, dict[str, Any]]]()
    bridge.commandReceived.connect(lambda cmd, payload: received_js_commands.append((cmd, payload)))

    bridge.dispatchFromJs(JsToPyCommand.OUTPUT_SELECT, json.dumps({"index": 1}))
    assert len(received_js_commands) == 1
    assert received_js_commands[0] == (JsToPyCommand.OUTPUT_SELECT, {"index": 1})

    # Non-dict JSON payload wrapped in a {"value": ...} dict
    bridge.dispatchFromJs(JsToPyCommand.RELOAD, "[1, 2, 3]")
    assert len(received_js_commands) == 2
    assert received_js_commands[1] == (JsToPyCommand.RELOAD, {"value": [1, 2, 3]})

    # Error handling: malformed JSON is safely dropped
    received_js_commands.clear()
    bridge.dispatchFromJs(JsToPyCommand.RELOAD, "invalid json")
    assert len(received_js_commands) == 0

    # Error handling: unknown command string is safely dropped
    bridge.dispatchFromJs("unregistered.command", "{}")
    assert len(received_js_commands) == 0


# Keybinding Serialization & Rules
@pytest.mark.parametrize(
    ("shortcut", "seq_str", "expected_rule"),
    [
        (
            GraphShortcut.FIT,
            "F",
            {"action": "fit", "key": "F", "ctrl": False, "shift": False, "alt": False, "meta": False},
        ),
        (
            GraphShortcut.TOGGLE_HEATMAP,
            "Ctrl+Shift+H",
            {"action": "toggleHeatmap", "key": "H", "ctrl": True, "shift": True, "alt": False, "meta": False},
        ),
        (
            GraphShortcut.CENTER_SELECTED,
            "Return",
            {"action": "centerSelected", "key": "Enter", "ctrl": False, "shift": False, "alt": False, "meta": False},
        ),
        (
            GraphShortcut.NAVIGATE_UP,
            "Up",
            {"action": "navigateUp", "key": "ArrowUp", "ctrl": False, "shift": False, "alt": False, "meta": False},
        ),
        (
            GraphShortcut.NAVIGATE_DOWN,
            "Down",
            {
                "action": "navigateDown",
                "key": "ArrowDown",
                "ctrl": False,
                "shift": False,
                "alt": False,
                "meta": False,
            },
        ),
    ],
)
def test_graph_shortcut_to_rule_valid(
    shortcut: GraphShortcut,
    seq_str: str,
    expected_rule: dict[str, Any],
) -> None:
    """Verify shortcut conversion translates QKeySequence into the DOM rules expected by the TS frontend."""
    rule = shortcut.to_rule(QKeySequence(seq_str))
    assert rule == expected_rule


@pytest.mark.skipif(os.getenv("CI", "").lower() in ["1", "true"], reason="CI segfault")
def test_graph_web_view_theme_sync(qtbot: QtBot) -> None:
    """Verify GraphWebView.sync_theme returns False before readiness and dispatches theme update when ready."""
    widget = QWidget()
    qtbot.addWidget(widget)
    view = GraphWebView(widget)

    # Before bridge is ready, sync_theme returns False without dispatch
    assert not view.bridge.is_ready
    assert view.sync_theme() is False

    # After bridge is ready, sync_theme dispatches THEME_UPDATE
    view.bridge.onWebReady()
    dispatched = list[str]()
    view.bridge.dispatchCommandSignal.connect(lambda cmd, _: dispatched.append(cmd))

    assert view.sync_theme() is True
    assert PyToJsCommand.THEME_UPDATE in dispatched


@pytest.mark.skipif(os.getenv("CI", "").lower() in ["1", "true"], reason="CI segfault")
def test_graph_web_view_palette_change_event(qtbot: QtBot) -> None:
    """Verify PaletteChange event updates background color and triggers theme synchronization."""
    widget = QWidget()
    qtbot.addWidget(widget)
    view = GraphWebView(widget)
    view.bridge.onWebReady()

    dispatched = list[str]()
    view.bridge.dispatchCommandSignal.connect(lambda cmd, _: dispatched.append(cmd))

    QCoreApplication.sendEvent(view, QEvent(QEvent.Type.PaletteChange))
    assert PyToJsCommand.THEME_UPDATE in dispatched


# Plugin Lifecycle & Initial State
def test_graph_plugin_initial_sync_on_web_ready(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Test full initial state sync (theme, options, outputs, keybindings, data) upon frontend readiness."""
    _, plugin = populated_harness

    received_dispatches = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received_dispatches.update({cmd: json.loads(payload_str) if payload_str else None})
    )

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)

    # Theme tokens dispatched
    assert PyToJsCommand.THEME_UPDATE in received_dispatches
    assert "isDark" in received_dispatches[PyToJsCommand.THEME_UPDATE]

    # Persistent options dispatched with defaults
    assert received_dispatches[PyToJsCommand.SET_OPTIONS] == {
        "direction": "TB",
        "layout": "dagre",
        "rankSep": 60,
        "nodeSep": 40,
        "heatmap": False,
        "showTimings": True,
        "timingUnit": "auto",
        "autoFitOnReload": True,
    }

    # Video outputs dispatched
    assert received_dispatches[PyToJsCommand.SET_OUTPUTS]["outputs"] == [
        {"index": 0, "name": "Clip 0", "isActive": True}
    ]

    # Keybindings rules dispatched
    assert PyToJsCommand.SET_KEYBINDINGS in received_dispatches
    assert len(received_dispatches[PyToJsCommand.SET_KEYBINDINGS]["keybindings"]) == len(GraphShortcut)

    # Initial graph topology dispatched
    assert PyToJsCommand.SET_DATA in received_dispatches
    graph_data = received_dispatches[PyToJsCommand.SET_DATA]
    assert len(graph_data["nodes"]) >= 2  # BlankClip + Crop


def test_graph_plugin_unchanged_graph_is_idempotent(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Verify calling update_graph without layout changes or cache eviction dispatches nothing."""
    _, plugin = populated_harness
    received = list[str]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, _: received.append(cmd))

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received, timeout=2000)
    received.clear()

    fut = plugin.update_graph(force_layout=False)
    qtbot.waitUntil(fut.done, timeout=2000)
    assert PyToJsCommand.SET_DATA not in received
    assert PyToJsCommand.UPDATE_TIMINGS not in received


def test_graph_plugin_reload_command_updates_timings(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Verify explicit reload command pushes timing updates."""
    _, plugin = populated_harness
    received = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received.update({cmd: json.loads(payload_str) if payload_str else None})
    )

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received, timeout=2000)
    received.clear()

    plugin.view.bridge.dispatchFromJs(JsToPyCommand.RELOAD, "{}")
    qtbot.waitUntil(lambda: PyToJsCommand.UPDATE_TIMINGS in received, timeout=2000)
    assert "timings" in received[PyToJsCommand.UPDATE_TIMINGS]


def test_graph_plugin_command_ready_routing(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    mocker: MockerFixture,
) -> None:
    """Verify JsToPyCommand.READY routed from JS invokes _on_web_ready."""
    _, plugin = populated_harness
    spy = mocker.spy(plugin, "_on_web_ready")

    plugin.view.bridge.dispatchFromJs(JsToPyCommand.READY, "{}")
    assert spy.call_count == 1


# Settings Propagation
def test_graph_plugin_settings_propagation(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
) -> None:
    """Test that plugin settings reach the frontend and are re-sent when changed."""
    _, plugin = populated_harness

    received_dispatches = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received_dispatches.update({cmd: json.loads(payload_str) if payload_str else None})
    )

    # Calling sync before ready does nothing
    plugin._sync_web_options()
    plugin._sync_keybindings()
    assert PyToJsCommand.SET_OPTIONS not in received_dispatches

    plugin.view.bridge.onWebReady()
    assert received_dispatches[PyToJsCommand.SET_OPTIONS]["direction"] == "TB"

    plugin.update_global_settings(
        direction="LR",
        heatmap_enabled=True,
        show_timings=False,
        timing_unit="ms",
        auto_fit_on_reload=False,
    )
    received_dispatches.clear()
    SettingsManager.signals.globalChanged.emit()

    assert received_dispatches[PyToJsCommand.SET_OPTIONS] == {
        "direction": "LR",
        "layout": "dagre",
        "rankSep": 60,
        "nodeSep": 40,
        "heatmap": True,
        "showTimings": False,
        "timingUnit": "ms",
        "autoFitOnReload": False,
    }


# Graph Inspection Caching & Invariants
def test_graph_plugin_visibility_and_dirty_deferral(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """When the plugin is hidden, graph updates are deferred until the widget is shown again."""
    _, plugin = populated_harness
    dispatched = list[str]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, _: dispatched.append(cmd))

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in dispatched, timeout=2000)

    plugin.hide()
    assert not plugin.isVisible()
    dispatched.clear()

    # Hidden update marks dirty without dispatching
    fut = plugin.update_graph(force_layout=False)
    qtbot.waitUntil(fut.done, timeout=2000)
    assert getattr(plugin, "_is_dirty") is True
    assert len(dispatched) == 0

    # Showing the widget flushes the dirty state
    plugin.show()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in dispatched, timeout=2000)
    assert getattr(plugin, "_is_dirty") is False


def test_graph_plugin_playback_suppression(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """During playback, graph updates are skipped to preserve rendering performance."""
    harness, plugin = populated_harness
    dispatched = list[str]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, _: dispatched.append(cmd))

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in dispatched, timeout=2000)
    dispatched.clear()

    harness.playback_proxy._state.is_playing = True
    fut = plugin.update_graph(force_layout=True)
    qtbot.waitUntil(fut.done, timeout=2000)
    assert PyToJsCommand.SET_DATA not in dispatched


# Node Selection Persistence & Invalidation
def test_graph_plugin_node_selection_lifecycle(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Test node selection re-application across layout updates, deselection, and orphan eviction."""
    _, plugin = populated_harness

    received_dispatches = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received_dispatches.update({cmd: json.loads(payload_str) if payload_str else None})
    )

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    graph_data = received_dispatches[PyToJsCommand.SET_DATA]
    selected_id = str(graph_data["nodes"][0]["data"]["id"])

    # 1. Selecting a node preserves selection on subsequent topology updates
    plugin.view.bridge.dispatchFromJs(JsToPyCommand.NODE_SELECT, json.dumps({"id": selected_id}))
    received_dispatches.clear()
    plugin.update_graph(force_layout=True)
    qtbot.waitUntil(
        lambda: (
            PyToJsCommand.SET_DATA in received_dispatches
            and received_dispatches.get(PyToJsCommand.SELECT_NODE) == {"id": selected_id}
        ),
        timeout=2000,
    )

    # 2. Deselecting a node removes selection re-application
    plugin.view.bridge.dispatchFromJs(JsToPyCommand.NODE_DESELECT, "{}")
    received_dispatches.clear()
    plugin.update_graph(force_layout=True)
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    assert PyToJsCommand.SELECT_NODE not in received_dispatches

    # 3. A selection pointing to an orphan node is dropped and not re-applied
    plugin.view.bridge.dispatchFromJs(JsToPyCommand.NODE_SELECT, json.dumps({"id": "nonexistent_node"}))
    received_dispatches.clear()
    plugin.update_graph(force_layout=True)
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    assert PyToJsCommand.SELECT_NODE not in received_dispatches


# Output Selection & Auto-Follow Mode
def test_graph_plugin_output_selection_and_follow_mode(
    qtbot: QtBot,
    tmp_path: Path,
    graph_env: ManagedEnvironment,
) -> None:
    """Test switching between outputs via bridge command and resuming auto-follow mode."""
    with graph_env.use():
        clip0 = graph_env.core.std.BlankClip(width=64, height=64, length=10, format=vs.RGB24)
        clip1 = graph_env.core.std.BlankClip(width=128, height=128, length=10, format=vs.RGB24)
        clip1 = graph_env.core.std.Invert(clip1)
        voutput0 = make_video_output(clip0, index=0)
        voutput1 = make_video_output(clip1, index=1)

    harness = HarnessPluginController(qtbot, tmp_path, env=graph_env)
    harness.set_current_voutput(voutput0)
    harness.set_current_voutput(voutput1)
    harness.set_current_voutput(voutput0)

    plugin = harness.create_plugin(GraphPlugin)
    qtbot.addWidget(plugin)

    received_dispatches = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received_dispatches.update({cmd: json.loads(payload_str) if payload_str else None})
    )

    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    data_v0 = received_dispatches[PyToJsCommand.SET_DATA]
    assert len(data_v0["nodes"]) == 1

    outputs_v0 = {item["index"]: item["isActive"] for item in received_dispatches[PyToJsCommand.SET_OUTPUTS]["outputs"]}
    assert outputs_v0 == {0: True, 1: False}

    # Select second output via bridge command
    received_dispatches.clear()
    plugin.view.bridge.dispatchFromJs(JsToPyCommand.OUTPUT_SELECT, json.dumps({"index": 1}))
    assert plugin.selected_output_index == 1
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    data_v1 = received_dispatches[PyToJsCommand.SET_DATA]
    assert len(data_v1["nodes"]) == 2  # BlankClip + Invert

    outputs_v1 = {item["index"]: item["isActive"] for item in received_dispatches[PyToJsCommand.SET_OUTPUTS]["outputs"]}
    assert outputs_v1 == {0: False, 1: True}

    # While locked to output 1, workspace current output changes do not switch inspected graph
    received_dispatches.clear()
    plugin.on_current_voutput_changed(harness.current_voutput, 0)
    assert PyToJsCommand.SET_DATA not in received_dispatches

    # Resetting output selection resumes following active video output
    received_dispatches.clear()
    plugin.select_output(None)
    assert getattr(plugin, "selected_output_index") is None
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)
    data_reset = received_dispatches[PyToJsCommand.SET_DATA]
    assert len(data_reset["nodes"]) == 1

    # Selecting an invalid negative index also resets to auto-follow mode
    plugin.select_output(1)
    assert getattr(plugin, "selected_output_index") == 1
    plugin.view.bridge.dispatchFromJs(JsToPyCommand.OUTPUT_SELECT, json.dumps({"index": -1}))
    assert getattr(plugin, "selected_output_index") is None


# Export & Image Operations
@pytest.mark.parametrize("fmt", [ExportFormat.DOT, ExportFormat.MERMAID, ExportFormat.ASCII, ExportFormat.JSON])
def test_graph_plugin_export_graph_formats(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    mocker: MockerFixture,
    tmp_path: Path,
    qtbot: QtBot,
    fmt: ExportFormat,
) -> None:
    """Test exporting graph in all supported text formats to both clipboard and disk."""
    _, plugin = populated_harness
    dispatched = list[str]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, _: dispatched.append(cmd))
    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in dispatched, timeout=2000)

    # 1. Export to clipboard
    QApplication.clipboard().clear()
    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.EXPORT_GRAPH,
        json.dumps({"format": fmt.value, "clipboard": True}),
    )

    match fmt:
        case ExportFormat.DOT:
            qtbot.waitUntil(lambda: "digraph" in QApplication.clipboard().text(), timeout=2000)
            assert "digraph" in QApplication.clipboard().text()
        case ExportFormat.MERMAID:
            qtbot.waitUntil(lambda: QApplication.clipboard().text().startswith("flowchart"), timeout=2000)
            assert QApplication.clipboard().text().startswith("flowchart")
        case ExportFormat.ASCII:
            qtbot.waitUntil(lambda: "BlankClip" in QApplication.clipboard().text(), timeout=2000)
            assert "BlankClip" in QApplication.clipboard().text()
        case ExportFormat.JSON:
            qtbot.waitUntil(lambda: QApplication.clipboard().text().startswith("{"), timeout=2000)
            assert json.loads(QApplication.clipboard().text())

    # 2. Export to file
    out_file = tmp_path / f"export{fmt.suffix}"
    mocker.patch.object(QFileDialog, "getSaveFileName", return_value=(str(out_file), ""))

    plugin.view.bridge.dispatchFromJs(JsToPyCommand.EXPORT_GRAPH, json.dumps({"format": fmt.value, "clipboard": False}))
    qtbot.waitUntil(lambda: out_file.exists(), timeout=2000)
    file_text = out_file.read_text(encoding="utf-8")

    match fmt:
        case ExportFormat.DOT:
            assert "digraph" in file_text
        case ExportFormat.MERMAID:
            assert file_text.startswith("flowchart")
        case ExportFormat.ASCII:
            assert len(file_text.strip()) > 0
        case ExportFormat.JSON:
            assert json.loads(file_text)


def test_graph_context_menu_structure_and_actions(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    mocker: MockerFixture,
    tmp_path: Path,
    qtbot: QtBot,
) -> None:
    """Verify GraphPlugin context menu actions, submenus, and triggering."""
    _, plugin = populated_harness
    menu = plugin.context_menu

    action_map = {act.text(): act for act in menu.actions()}

    dispatched = list[tuple[str, str]]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, payload: dispatched.append((cmd, payload)))

    # Test root actions
    action_map["Fit to View"].trigger()
    assert dispatched[-1][0] == PyToJsCommand.FIT_VIEW

    action_map["Reset View"].trigger()
    assert dispatched[-1][0] == PyToJsCommand.RESET_VIEW

    mock_outputs = mocker.patch.object(plugin, "update_outputs")
    mock_graph = mocker.patch.object(plugin, "update_graph")
    action_map["Reload Graph"].trigger()
    mock_outputs.assert_called_once()
    mock_graph.assert_called_once_with(refresh=True)

    # Copy Submenu
    copy_menu = action_map["Copy Graph as"].menu()
    assert isinstance(copy_menu, QMenu)
    copy_action_map = {act.text(): act for act in copy_menu.actions()}
    assert "PNG Image" in copy_action_map
    assert "DOT" in copy_action_map
    assert "Mermaid" in copy_action_map
    assert "ASCII" in copy_action_map
    assert "JSON" in copy_action_map

    copy_action_map["PNG Image"].trigger()
    assert dispatched[-1][0] == PyToJsCommand.REQUEST_PNG_EXPORT
    assert json.loads(dispatched[-1][1]) == {"destination": "clipboard"}

    QApplication.clipboard().clear()
    copy_action_map["DOT"].trigger()
    qtbot.waitUntil(lambda: "digraph" in QApplication.clipboard().text(), timeout=2000)
    assert "digraph" in QApplication.clipboard().text()

    # Save Submenu
    save_menu = action_map["Save Graph as"].menu()
    assert isinstance(save_menu, QMenu)
    save_action_map = {act.text(): act for act in save_menu.actions()}
    assert "PNG Image…" in save_action_map
    assert "DOT…" in save_action_map
    assert "Mermaid…" in save_action_map
    assert "ASCII…" in save_action_map
    assert "JSON…" in save_action_map

    png_path = tmp_path / "menu_save.png"
    mocker.patch.object(QFileDialog, "getSaveFileName", return_value=(str(png_path), ""))
    save_action_map["PNG Image…"].trigger()
    assert dispatched[-1][0] == PyToJsCommand.REQUEST_PNG_EXPORT
    assert json.loads(dispatched[-1][1]) == {"destination": "file", "path": str(png_path)}

    dot_path = tmp_path / "menu_export.dot"
    mocker.patch.object(QFileDialog, "getSaveFileName", return_value=(str(dot_path), ""))
    save_action_map["DOT…"].trigger()
    qtbot.waitUntil(lambda: dot_path.exists(), timeout=2000)
    assert "digraph" in dot_path.read_text(encoding="utf-8")


def test_graph_plugin_context_menu_dispatch(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    mocker: MockerFixture,
) -> None:
    """Verify contextmenu.request from JS opens the context menu."""
    _, plugin = populated_harness
    mock_exec = mocker.patch.object(plugin.context_menu, "exec")

    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.CONTEXT_MENU,
        json.dumps({"x": 100, "y": 150, "target": "background"}),
    )
    mock_exec.assert_called_once()


def test_graph_plugin_canvas_actions_and_png_export(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
) -> None:
    """Verify fit_view, reset_view, prompt_save_png_image, and copy_png_image bridge dispatches."""
    _, plugin = populated_harness
    dispatched = list[tuple[str, str]]()
    plugin.view.bridge.dispatchCommandSignal.connect(lambda cmd, payload: dispatched.append((cmd, payload)))

    plugin.fit_act.trigger()
    assert dispatched[-1][0] == PyToJsCommand.FIT_VIEW

    plugin.reset_act.trigger()
    assert dispatched[-1][0] == PyToJsCommand.RESET_VIEW


def test_graph_plugin_copy_image_to_clipboard(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Verify SAVE_IMAGE with destination=clipboard copies the image to clipboard."""
    _, plugin = populated_harness

    # Create a valid 2x2 test image and encode to PNG bytes
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")  # type: ignore[call-overload]
    png_bytes = bytes(buffer.data().data())
    b64_str = "data:image/png;base64," + base64.b64encode(png_bytes).decode()

    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.SAVE_IMAGE,
        json.dumps({"format": "png", "dataBase64": b64_str, "destination": "clipboard"}),
    )

    qtbot.waitUntil(lambda: not QApplication.clipboard().image().isNull(), timeout=2000)


# Node Call Reconstruction & Diagnostic Report
def _make_video_media_info(
    *,
    format_id: vs.PresetVideoFormat = vs.PresetVideoFormat.YUV420P8,
    width: int = 1920,
    height: int = 1080,
    fps_num: int = 24000,
    fps_den: int = 1001,
    num_frames: int = 1000,
) -> vsgraph.VideoMediaInfo:
    return vsgraph.VideoMediaInfo(
        format_id=format_id,
        width=width,
        height=height,
        fps_num=fps_num,
        fps_den=fps_den,
        num_frames=num_frames,
    )


def _make_graph_node(
    node_id: str = "node_1",
    name: str = "BlankClip",
    *,
    filter_mode: vs.FilterMode = vs.FilterMode.PARALLEL,
    processing_time_ns: int = 1_000_000,
    media_info: vsgraph.VideoMediaInfo | None = None,
    call_stack: list[vsgraph.CallFrame] | None = None,
) -> vsgraph.Node[vsgraph.VideoMediaInfo]:
    return vsgraph.Node(
        id=node_id,
        name=name,
        filter_mode=filter_mode,
        processing_time_ns=processing_time_ns,
        media_info=media_info or _make_video_media_info(),
        call_stack=call_stack if call_stack is not None else [],
    )


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("abc", '"abc"'),
        (3, "3"),
        (2.5, "2.5"),
        (True, "True"),
        (False, "False"),
        (None, "None"),
        ([1, "a", False], '[1, "a", False]'),
        ({"a": 1, "b": True, "c": None}, '{"a": 1, "b": True, "c": None}'),
        (vsgraph.VideoNodeRef(id="42"), '{"id": "42", "type": "vnode"}'),
        (vsgraph.FuncRef(id="fn_1"), '{"id": "fn_1", "type": "func"}'),
    ],
)
def test_format_python_value(raw_value: Any, expected: str) -> None:
    assert format_python_value(raw_value) == expected


def test_build_filter_call_variations(graph_env: ManagedEnvironment) -> None:
    """Verify filter call reconstruction with single clips, multiple clips, clip lists, and empty stacks."""
    with graph_env.use():
        single_node = _make_graph_node(
            call_stack=[
                vsgraph.CallFrame(
                    level=0,
                    id="frame_1",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="Crop",
                    arguments={"clip": vsgraph.VideoNodeRef(id="42"), "left": 8, "right": 8},
                )
            ]
        )
        assert build_filter_call(single_node) == "core.std.Crop(clip, left=8, right=8)"

        merge_node = _make_graph_node(
            call_stack=[
                vsgraph.CallFrame(
                    level=0,
                    id="frame_1",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="Merge",
                    arguments={
                        "clipa": vsgraph.VideoNodeRef(id="1"),
                        "clipb": {"id": "2", "type": "vnode"},
                        "weight": 0.5,
                    },
                )
            ]
        )
        assert build_filter_call(merge_node) == "core.std.Merge(clip, clip2, weight=0.5)"

        splice_node = _make_graph_node(
            call_stack=[
                vsgraph.CallFrame(
                    level=0,
                    id="frame_1",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="Splice",
                    arguments={
                        "clips": [vsgraph.VideoNodeRef(id="1"), vsgraph.VideoNodeRef(id="2")],
                        "mismatch": True,
                    },
                )
            ]
        )
        assert build_filter_call(splice_node) == "core.std.Splice(clips=[clip, clip2], mismatch=True)"

        empty_node = _make_graph_node(node_id="node_9", name="Mystery", call_stack=[])
        assert build_filter_call(empty_node) == "# Mystery [node_9]: no call frame recorded"

        stacked_node = _make_graph_node(
            node_id="node_10",
            name="Resize",
            call_stack=[
                vsgraph.CallFrame(
                    level=0,
                    id="frame_1",
                    plugin_id="com.vapoursynth.resize",
                    plugin_ns="resize",
                    function="Bicubic",
                    arguments={"clip": vsgraph.VideoNodeRef(id="1"), "width": 1280},
                ),
                vsgraph.CallFrame(
                    level=1,
                    id="frame_2",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="Crop",
                    arguments={"clip": vsgraph.VideoNodeRef(id="1"), "top": 4},
                ),
            ],
        )
        assert build_filter_call(stacked_node) == "core.resize.Bicubic(clip, width=1280)"
        assert build_filter_call(stacked_node, 1) == "core.std.Crop(clip, top=4)"
        assert build_filter_call(stacked_node, 2) == "# Resize [node_10]: no call frame at index 2"
        assert build_filter_call(stacked_node, -1) == "# Resize [node_10]: no call frame at index -1"


def test_build_node_report(graph_env: ManagedEnvironment) -> None:
    """Verify plain-text node report formatting for root and non-root nodes."""
    with graph_env.use():
        node = _make_graph_node(
            node_id="node_1",
            name="Crop",
            processing_time_ns=2_500_000,
            call_stack=[
                vsgraph.CallFrame(
                    level=0,
                    id="frame_1",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="Crop",
                    arguments={"left": 8, "top": 4},
                ),
                vsgraph.CallFrame(
                    level=1,
                    id="frame_2",
                    plugin_id="com.vapoursynth.std",
                    plugin_ns="std",
                    function="BlankClip",
                    arguments={},
                ),
            ],
        )
        report = build_node_report(node, is_root=True)

        assert "Crop [node_1]" in report
        assert "Mode: PARALLEL" in report
        assert "Time: 2.50 ms" in report
        assert "Media: YUV420P8 1920x1080 @ 24000/1001 fps, 1000 frames" in report
        assert "Output root: yes" in report
        assert "  [0] std.Crop (com.vapoursynth.std)" in report
        assert "      left = 8" in report
        assert "      top = 4" in report
        assert "  [1] std.BlankClip (com.vapoursynth.std)" in report

        empty_report = build_node_report(_make_graph_node(call_stack=[]), is_root=False)
        assert "Output root: no" in empty_report
        assert "  (none recorded)" in empty_report


# Static Graph Analysis
def test_describe_media_delta_and_transitions(graph_env: ManagedEnvironment) -> None:
    """Verify media delta detection, rational FPS equivalence, and transition grouping."""
    with graph_env.use():
        base_info = _make_video_media_info()
        assert describe_media_delta(base_info, _make_video_media_info()) is None

        # Equivalent rational frame rates (24/1 == 48/2) produce no delta
        assert (
            describe_media_delta(
                _make_video_media_info(fps_num=24, fps_den=1),
                _make_video_media_info(fps_num=48, fps_den=2),
            )
            is None
        )

        # Zero denominator handled safely
        zero_den_delta = describe_media_delta(
            _make_video_media_info(fps_num=0, fps_den=0),
            _make_video_media_info(fps_num=24, fps_den=1),
        )
        assert zero_den_delta == {"fields": ["fps"], "label": "0→24 fps"}

        # Full delta across all 4 fields
        full_delta = describe_media_delta(
            base_info,
            _make_video_media_info(
                format_id=vs.PresetVideoFormat.YUV420P10,
                width=3840,
                height=2160,
                fps_num=30000,
                fps_den=1001,
                num_frames=500,
            ),
        )
        assert full_delta == {
            "fields": ["format", "resolution", "fps", "frames"],
            "label": "YUV420P8→YUV420P10, 1920x1080→3840x2160, 23.976→29.97 fps, 1000→500 frames",
        }

        # Collect and group transitions on a 4-node graph (including a dangling edge skipped safely)
        p10_info = _make_video_media_info(format_id=vs.PresetVideoFormat.YUV420P10)
        graph = vsgraph.Graph(
            root_id="d",
            nodes=[
                _make_graph_node("a", media_info=base_info),
                _make_graph_node("b", media_info=p10_info),
                _make_graph_node("c", media_info=p10_info),
                _make_graph_node("d", media_info=p10_info),
            ],
            edges=[
                vsgraph.Edge(source="a", target="b", pattern=vsgraph.RequestPattern.GENERAL, index=0),
                vsgraph.Edge(source="b", target="c", pattern=vsgraph.RequestPattern.GENERAL, index=0),
                vsgraph.Edge(source="a", target="d", pattern=vsgraph.RequestPattern.GENERAL, index=0),
                vsgraph.Edge(source="a", target="missing", pattern=vsgraph.RequestPattern.GENERAL, index=0),
            ],
        )
        transitions = collect_media_transitions(graph)
        assert [t["targetId"] for t in transitions] == ["b", "d"]

        groups = group_media_transitions(transitions)
        assert len(groups) == 1
        assert groups[0]["signature"] == "YUV420P8→YUV420P10"
        assert groups[0]["targetNodeIds"] == ["b", "d"]


def test_compute_depth() -> None:
    """Verify Kahn topological longest-path depth across empty, branching, and cyclic graphs."""
    assert compute_depth(vsgraph.Graph(root_id="", nodes=[], edges=[])) == 0
    assert compute_depth(vsgraph.Graph(root_id="a", nodes=[_make_graph_node("a")], edges=[])) == 1

    # Branching DAG: a -> b -> c -> d (length 4) and shortcut a -> d
    dag = vsgraph.Graph(
        root_id="d",
        nodes=[_make_graph_node(nid) for nid in ("a", "b", "c", "d")],
        edges=[
            vsgraph.Edge(source="a", target="b", pattern=vsgraph.RequestPattern.GENERAL, index=0),
            vsgraph.Edge(source="b", target="c", pattern=vsgraph.RequestPattern.GENERAL, index=0),
            vsgraph.Edge(source="c", target="d", pattern=vsgraph.RequestPattern.GENERAL, index=0),
            vsgraph.Edge(source="a", target="d", pattern=vsgraph.RequestPattern.GENERAL, index=1),
            vsgraph.Edge(source="a", target="missing", pattern=vsgraph.RequestPattern.GENERAL, index=0),
        ],
    )
    assert compute_depth(dag) == 4

    # Cyclic graph terminates safely
    cyclic = vsgraph.Graph(
        root_id="b",
        nodes=[_make_graph_node("a"), _make_graph_node("b")],
        edges=[
            vsgraph.Edge(source="a", target="b", pattern=vsgraph.RequestPattern.GENERAL, index=0),
            vsgraph.Edge(source="b", target="a", pattern=vsgraph.RequestPattern.GENERAL, index=0),
        ],
    )
    assert compute_depth(cyclic) == 1


def test_collect_and_group_warnings() -> None:
    """Verify concurrency and request-pattern warning collection and grouping."""
    graph = vsgraph.Graph(
        root_id="n_state",
        nodes=[
            _make_graph_node("n_unordered", "Unord", filter_mode=vs.FilterMode.UNORDERED),
            _make_graph_node("n_state", "State", filter_mode=vs.FilterMode.FRAME_STATE),
        ],
        edges=[
            vsgraph.Edge(
                source="n_unordered", target="n_state", pattern=vsgraph.RequestPattern.STRICT_SPATIAL, index=0
            ),
            vsgraph.Edge(
                source="n_unordered", target="n_state", pattern=vsgraph.RequestPattern.NO_FRAME_REUSE, index=1
            ),
        ],
    )

    warnings = collect_warnings(graph)
    assert [(w["code"], w["severity"]) for w in warnings] == [
        ("framestate", Severity.WARNING),
        ("no-frame-reuse", Severity.WARNING),
        ("strict-spatial", Severity.INFO),
        ("unordered", Severity.INFO),
    ]

    groups = group_warnings(warnings)
    assert [g["code"] for g in groups] == ["framestate", "no-frame-reuse", "unordered", "strict-spatial"]
    assert groups[0]["title"] == "Frame-state serialization"
    assert groups[1]["title"] == "No input frame reuse"


def test_enrich_cytoscape_payload(graph_env: ManagedEnvironment) -> None:
    """Verify enrich_cytoscape_payload annotates edge media deltas and populates payload['analysis']."""
    with graph_env.use():
        clip = graph_env.core.std.BlankClip(width=128, height=128, length=20, format=vs.RGB24)
        clip = graph_env.core.std.Crop(clip, left=8, right=8)
        graph = vsgraph.get_graph(clip)
        payload = enrich_cytoscape_payload(graph, graph.to_cytoscape())

    assert "analysis" in payload
    analysis = payload["analysis"]
    assert analysis["nodeCount"] == 2
    assert analysis["edgeCount"] == 1
    assert analysis["depth"] == 2
    assert analysis["transitionCount"] == 1
    assert analysis["transitionGroups"][0]["signature"] == "128x128→112x128"
    assert payload["edges"][0]["data"]["media_label"] == "128x128→112x128"
    assert payload["edges"][0]["data"]["media_fields"] == ["resolution"]


def test_graph_plugin_node_copy_command(
    populated_harness: tuple[HarnessPluginController, GraphPlugin],
    qtbot: QtBot,
) -> None:
    """Verify JsToPyCommand.NODE_COPY copies node ID, filter call, and node report to clipboard."""
    _, plugin = populated_harness

    received_dispatches = dict[str, Any]()
    plugin.view.bridge.dispatchCommandSignal.connect(
        lambda cmd, payload_str: received_dispatches.update({cmd: json.loads(payload_str) if payload_str else None})
    )
    plugin.view.bridge.onWebReady()
    qtbot.waitUntil(lambda: PyToJsCommand.SET_DATA in received_dispatches, timeout=2000)

    graph_data = received_dispatches[PyToJsCommand.SET_DATA]
    crop_node = next(n["data"] for n in graph_data["nodes"] if n["data"]["name"] == "Crop")
    node_id = str(crop_node["id"])

    # 1. Copy node ID
    QApplication.clipboard().clear()
    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.NODE_COPY,
        json.dumps({"id": node_id, "target": NodeCopyTarget.ID.value}),
    )
    qtbot.waitUntil(lambda: QApplication.clipboard().text() == node_id, timeout=2000)

    # 2. Copy reconstructed Python filter call
    QApplication.clipboard().clear()
    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.NODE_COPY,
        json.dumps({"id": node_id, "target": NodeCopyTarget.CALL.value, "frameIdx": 0}),
    )
    qtbot.waitUntil(lambda: QApplication.clipboard().text().startswith("core.std.Crop(clip,"), timeout=2000)
    copied_call = QApplication.clipboard().text()
    assert "left=8" in copied_call
    assert "right=8" in copied_call

    # 3. Copy full diagnostic node report
    QApplication.clipboard().clear()
    plugin.view.bridge.dispatchFromJs(
        JsToPyCommand.NODE_COPY,
        json.dumps({"id": node_id, "target": NodeCopyTarget.REPORT.value}),
    )
    qtbot.waitUntil(lambda: f"Crop [{node_id}]" in QApplication.clipboard().text(), timeout=2000)
    copied_report = QApplication.clipboard().text()
    assert "Output root: yes" in copied_report
    assert "std.Crop (com.vapoursynth.std)" in copied_report
