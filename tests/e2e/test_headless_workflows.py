from __future__ import annotations

import pkgutil
from collections.abc import Callable, Generator
from concurrent.futures import Future
from importlib import import_module
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from vsview.app import tools
from vsview.app.main import MainWindow
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins import specs
from vsview.app.plugins.manager import PluginManager
from vsview.app.tools.colorpicker.plugin import ColorPickerPlugin
from vsview.app.tools.cropping.plugin import RegionSelectorPlugin
from vsview.app.tools.scening.plugin import SceningPlugin
from vsview.app.tools.scening.serializer import QPFileSerializer
from vsview.app.workspace import PythonScriptWorkspace, VideoFileWorkspace
from vsview.vsenv import QtEventLoop, unregister_policy

pytestmark = [pytest.mark.integration, pytest.mark.qt, pytest.mark.vpy("no-policy")]


@pytest.fixture
def plugin_manager() -> PluginManager:
    """Initialize PluginManager and register all first-party tool docks and panels."""
    pm = PluginManager()
    pm._entry_points_loaded = True
    if not hasattr(pm.manager.hook, "vsview_get_video_processor"):
        pm.manager.add_hookspecs(specs)
    for _, name, _ in pkgutil.iter_modules(tools.__path__):
        if not name.startswith("_"):
            mod = import_module(f"vsview.app.tools.{name}")
            if not pm.manager.is_registered(mod):
                pm.manager.register(mod, name=name)
    if pm._load_future is None:
        f = Future[None]()
        f.set_result(None)
        pm._load_future = f
    return pm


@pytest.fixture
def main_window(
    qapp: QApplication,
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
    plugin_manager: PluginManager,
) -> MainWindow:
    window = MainWindow()
    window.stack.animations_enabled = False
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture(autouse=True)
def ensure_no_policy() -> Generator[None]:
    try:
        yield
    finally:
        unregister_policy()


def test_e2e_complete_user_journey(
    main_window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    vpy_script_factory: Callable[..., Path],
) -> None:
    # 1. Arrange: Write 100-frame script (0..23 Red, 24..47 Green, 48..99 Blue)
    script_file = vpy_script_factory(length=100)

    # Act: Load script into workspace
    btn, fut = main_window.load_new_script(script_file)
    ws = btn.workspace

    qtbot.waitUntil(
        lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page and ws.playback.can_reload,
        timeout=5000,
    )
    assert fut.result() == 0

    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 100

    # 3. Timeline scrubbing: Seek to frame 24 (Green segment)
    ws.playback.request_frame(24)
    qtbot.waitUntil(
        lambda: ws.playback.state.current_frame == 24 and ws.playback.can_reload,
        timeout=3000,
    )
    assert ws.playback.state.current_frame == 24
    assert ws.tbar.playback_container.frame_edit.value() == 24

    # 4. Open and initialize Tool Docks in UI
    ws.dock_toggle_btn.setChecked(True)

    # 5. Tool Inspection: Color Picker
    color_picker = next(p for p in ws.plugins if isinstance(p, ColorPickerPlugin))
    with ws.api.vs_context():
        color_picker.on_current_voutput_changed(ws.api.current_voutput, ws.api.current_video_index)
    color_picker.eyedropper_btn.setChecked(True)

    view = ws.tab_manager.current_view
    scene_pt = view.pixmap_item.mapToScene(QPointF(10, 10))
    viewport_pt = view.mapFromScene(scene_pt)

    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(viewport_pt),
        QPointF(viewport_pt),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    color_picker.on_view_mouse_moved(move_event)
    qtbot.waitUntil(lambda: color_picker.position_label.cursor_pos == QPoint(10, 10), timeout=3000)

    # Assert exact sampled Green color (R=0, G=255, B=0)
    assert color_picker.position_label.cursor_pos == QPoint(10, 10)
    assert color_picker.rgb_labels["Dec"][0].text() == "0"
    assert color_picker.rgb_labels["Dec"][1].text() == "255"
    assert color_picker.rgb_labels["Dec"][2].text() == "0"
    assert color_picker.rgb_labels["Hex"][1].text() == "FF"

    # 6. Region Selection / Cropping Tool
    crop_plugin = next(p for p in ws.plugins if isinstance(p, RegionSelectorPlugin))
    with ws.api.vs_context():
        crop_plugin.on_current_voutput_changed(ws.api.current_voutput, ws.api.current_video_index)
    crop_plugin.enable_btn.setChecked(True)

    crop_plugin.on_view_rect_selection_changed(QRect(8, 8, 32, 32))
    qtbot.waitUntil(lambda: crop_plugin.crop_rect == QRect(8, 8, 32, 32), timeout=3000)

    assert crop_plugin.crop_rect == QRect(8, 8, 32, 32)
    assert crop_plugin.left_box.value() == 8
    assert crop_plugin.top_box.value() == 8
    assert crop_plugin.width_box.value() == 32
    assert crop_plugin.height_box.value() == 32
    assert crop_plugin.command_label.text() == "std.Crop(8, 24, 8, 24)"

    # 7. Scening / Bookmarking Workflow & Serialization
    scening_plugin = next(p for p in ws.plugins if isinstance(p, SceningPlugin))
    scening_plugin.on_new_scene()
    qtbot.waitUntil(lambda: scening_plugin.scenes_model.rowCount() == 1, timeout=2000)
    assert scening_plugin.scenes_model.rowCount() == 1

    # Add bookmark at current frame (frame 24)
    scening_plugin.on_add_frame_triggered()
    qtbot.waitUntil(lambda: scening_plugin.ranges_model.rowCount() == 1, timeout=2000)
    assert scening_plugin.ranges_model.rowCount() == 1

    # Export scene via QPFileSerializer
    export_file = tmp_path / "scenes_export.qp"
    scening_plugin.serialize_exported_scene(str(export_file), QPFileSerializer())
    qtbot.waitUntil(lambda: export_file.is_file(), timeout=3000)

    assert export_file.is_file()
    assert export_file.read_text(encoding="utf-8") == "24 K\n"


def test_e2e_multi_tab_lifecycle_and_disposal(
    main_window: MainWindow,
    qtbot: QtBot,
    vpy_script_factory: Callable[..., Path],
) -> None:
    # 1. Arrange: Dual-output script (Output 0: 64x64, 48 frames; Output 1: 128x128, 96 frames)
    script_file = vpy_script_factory(length=48, dual_output=True)

    # 2. Act: Load script
    btn, fut = main_window.load_new_script(script_file)
    ws = btn.workspace

    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=5000)
    assert fut.result() == 0

    # 3. Assert initial Output 0 tab state
    assert len(ws.outputs_manager.voutputs) == 2
    assert ws.tab_manager.tabs.count() == 2
    assert ws.outputs_manager.current_video_index == 0
    assert ws.tbar.total_frames == 48
    assert ws.tab_manager.current_view.pixmap_item.pixmap().width() == 64

    # 4. Switch to Output 1 tab
    ws.tab_manager.switch_tab(1)
    qtbot.waitUntil(
        lambda: (
            ws.outputs_manager.current_video_index == 1
            and ws.tab_manager.tabs.currentIndex() == 1
            and ws.outputs_manager.voutputs[1].loaded_once
            and ws.tab_manager.current_view.pixmap_item.pixmap().width() == 128
        ),
        timeout=3000,
    )

    # 5. Assert Output 1 properties updated on timeline & view
    assert ws.outputs_manager.current_video_index == 1
    assert ws.tbar.total_frames == 96
    assert ws.tab_manager.current_view.pixmap_item.pixmap().width() == 128

    # 6. Test Zoom synchronization across tabs
    ws.tab_manager.sync_zoom_btn.setChecked(True)
    ws.tab_manager.current_view.set_zoom(2.0)
    assert ws.tab_manager.current_view.current_zoom == 2.0

    # Switch back to Tab 0 and verify zoom is preserved via synchronization
    ws.tab_manager.switch_tab(0)
    qtbot.waitUntil(lambda: ws.outputs_manager.current_video_index == 0, timeout=3000)
    assert ws.tab_manager.current_view.current_zoom == 2.0

    # 7. Delete workspace and assert clean teardown
    main_window.delete_workspace(btn)
    assert len(main_window.nav_container.buttons) == 0
    assert main_window.stack.count() == 0


def test_e2e_hot_reload_lifecycle_in_gui(
    main_window: MainWindow,
    qtbot: QtBot,
    vpy_script_factory: Callable[..., Path],
) -> None:
    # 1. Arrange: Script with 100 frames
    script_file = vpy_script_factory(filename="reload_gui.vpy", length=100)

    # Act: Load script into workspace
    btn, fut = main_window.load_new_script(script_file)
    ws = btn.workspace

    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=5000)
    assert fut.result() == 0

    # 2. Seek to frame 30
    ws.playback.request_frame(30)
    qtbot.waitUntil(
        lambda: ws.playback.state.current_frame == 30 and ws.playback.can_reload,
        timeout=3000,
    )
    assert ws.playback.state.current_frame == 30
    assert ws.tbar.playback_container.frame_edit.value() == 30

    # 3. Modify script on disk to 120 frames
    vpy_script_factory(filename="reload_gui.vpy", length=120)

    # 4. Trigger hot reload via public workspace API
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.playback.can_reload, timeout=5000)
    assert fut_reload.result() == 0

    # 5. Assert frame position and UI state preserved
    assert ws.stack.currentWidget() is ws.loaded_page
    assert ws.playback.state.current_frame == 30
    assert ws.tbar.playback_container.frame_edit.value() == 30
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput.vs_output.clip.num_frames == 120


def test_e2e_multiple_concurrent_workspaces(
    main_window: MainWindow,
    qtbot: QtBot,
    fixture_video_file: Path,
    vpy_script_factory: Callable[..., Path],
) -> None:
    # 1. Arrange: Create a Python script file and locate fixture video file
    script_1 = vpy_script_factory(filename="ws1.vpy", length=50)

    # 2. Add Workspace 1 (Script) and load script
    btn1, fut1 = main_window.load_new_script(script_1)
    ws1 = btn1.workspace
    assert isinstance(ws1, PythonScriptWorkspace)
    qtbot.waitUntil(lambda: fut1.done() and ws1.stack.currentWidget() is ws1.loaded_page, timeout=5000)
    assert fut1.result() == 0
    assert isinstance(ws1.outputs_manager.current_voutput, VideoOutput)
    assert ws1.outputs_manager.current_voutput.vs_output.clip.num_frames == 50

    # 3. Add Workspace 2 (Video File) and load media via load_new_file
    btn2, fut2 = main_window.load_new_file(fixture_video_file)
    ws2 = btn2.workspace
    assert isinstance(ws2, VideoFileWorkspace)
    qtbot.waitUntil(lambda: fut2.done() and ws2.stack.currentWidget() is ws2.loaded_page, timeout=5000)
    assert fut2.result() == 0
    assert isinstance(ws2.outputs_manager.current_voutput, VideoOutput)
    assert ws2.outputs_manager.current_voutput.vs_output.clip.width == 1280

    # 4. Assert both heterogeneous workspaces registered in MainWindow
    assert len(main_window.nav_container.buttons) == 2
    assert main_window.stack.count() == 2
    assert main_window.stack.currentWidget() is ws2

    # 5. Switch active workspace to Workspace 1 (Script)
    main_window.nav_container.buttons[0].click()
    assert main_window.stack.currentWidget() is ws1

    # 6. Delete Workspace 1 -> MainWindow switches focus to Workspace 2 (Video)
    main_window.delete_workspace(btn1)
    assert len(main_window.nav_container.buttons) == 1
    assert main_window.nav_container.buttons[0] is btn2
    assert main_window.stack.count() == 1
    assert main_window.stack.currentWidget() is ws2

    # 7. Delete Workspace 2 -> MainWindow is empty and ready
    main_window.delete_workspace(btn2)
    assert len(main_window.nav_container.buttons) == 0
    assert main_window.stack.count() == 0
