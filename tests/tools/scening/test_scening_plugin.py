from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication
from vsengine.policy import ManagedEnvironment

from vsview.api import Frame
from vsview.app.outputs.video import VideoOutput
from vsview.app.tools.scening.models import RangeFrame
from vsview.app.tools.scening.plugin import SceningPlugin
from vsview.app.tools.scening.ui import ROLE_CHECK_STATE, Col, RangeCol

if TYPE_CHECKING:
    from tests.harness import HarnessPluginController

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


@pytest.fixture(autouse=True)
def _use_qt_event_loop(qt_event_loop: Any) -> Any:
    return qt_event_loop


@pytest.fixture
def plugin_controller(
    plugin_controller: HarnessPluginController,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> HarnessPluginController:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)
    plugin_controller.env = env
    plugin_controller.set_current_voutput(voutput)
    return plugin_controller


def test_scening_instantiation_and_new_scene(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()

    assert plugin.api is plugin_controller.api
    assert plugin.range_container.isEnabled() is False
    assert plugin.export_scene_action.isEnabled() is False
    assert plugin.scenes_model.rowCount() == 0

    plugin.on_new_scene()

    assert plugin.scenes_model.rowCount() == 1
    scene = plugin.scenes_model.scenes[0]
    assert scene.name == "New Scene 01"
    assert plugin.range_container.isEnabled() is True
    assert plugin.export_scene_action.isEnabled() is True

    # Verify settings persistence
    assert len(plugin.settings.local_.scenes) == 1
    assert plugin.settings.local_.scenes[0].name == "New Scene 01"


def test_scening_mark_in_out_and_range_creation(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    plugin_controller.set_current_frame(10)
    plugin.range_start_action.setChecked(True)
    assert plugin.add_range_action.isEnabled() is False

    plugin_controller.set_current_frame(30)
    plugin.range_end_action.setChecked(True)
    assert plugin.add_range_action.isEnabled() is True

    plugin.on_add_range_triggered()

    assert plugin.ranges_model.rowCount() == 1
    range_item, scene = plugin.ranges_model.ranges[0]
    assert isinstance(range_item, RangeFrame)
    assert range_item.as_frames(plugin_controller.current_voutput) == (10, 30)
    assert plugin.range_start_action.isChecked() is False
    assert plugin.range_end_action.isChecked() is False

    # Verify timeline notch was added
    notches = plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])
    assert len(notches) == 1
    assert notches[0][0] == [(10, 30)]
    assert notches[0][1] == scene.color


def test_scening_inverted_and_single_frame_ranges(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    # Inverted selection (start 80, end 40) automatically sorted
    plugin_controller.set_current_frame(80)
    plugin.range_start_action.setChecked(True)
    plugin_controller.set_current_frame(40)
    plugin.range_end_action.setChecked(True)
    plugin.on_add_range_triggered()

    range_item, _ = plugin.ranges_model.ranges[0]
    assert range_item.as_frames(plugin_controller.current_voutput) == (40, 80)

    # Single frame bookmark at frame 55
    plugin_controller.set_current_frame(55)
    plugin.on_add_frame_triggered()

    assert plugin.ranges_model.rowCount() == 2
    single_item, _ = plugin.ranges_model.ranges[1]
    assert single_item.as_frames(plugin_controller.current_voutput) == (55, 55)


def test_scening_boundary_seeking_navigation(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    # Add range [10, 20]
    plugin_controller.set_current_frame(10)
    plugin.range_start_action.setChecked(True)
    plugin_controller.set_current_frame(20)
    plugin.range_end_action.setChecked(True)
    plugin.on_add_range_triggered()

    # Add range [40, 60]
    plugin_controller.set_current_frame(40)
    plugin.range_start_action.setChecked(True)
    plugin_controller.set_current_frame(60)
    plugin.range_end_action.setChecked(True)
    plugin.on_add_range_triggered()

    # Forward seek across boundaries [10, 20, 40, 60]
    plugin_controller.set_current_frame(0)
    plugin.on_seek_next_bound()
    assert plugin_controller.current_frame == Frame(10)

    plugin.on_seek_next_bound()
    assert plugin_controller.current_frame == Frame(20)

    plugin.on_seek_next_bound()
    assert plugin_controller.current_frame == Frame(40)

    plugin.on_seek_next_bound()
    assert plugin_controller.current_frame == Frame(60)

    # Backward seek
    plugin.on_seek_prev_bound()
    assert plugin_controller.current_frame == Frame(40)

    plugin.on_seek_prev_bound()
    assert plugin_controller.current_frame == Frame(20)

    # Starts-only range seeking (should jump 10 -> 40, skipping 20)
    plugin_controller.set_current_frame(10)
    plugin.on_seek_next_range()
    assert plugin_controller.current_frame == Frame(40)

    plugin.on_seek_prev_range()
    assert plugin_controller.current_frame == Frame(10)


def test_scening_exclusive_bounds_and_shortcut_toggles(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    # Default inclusive mode: last frame is num_frames - 1 (99)
    plugin.on_first_frame_toggle()
    assert plugin._pending_start == Frame(0)
    assert plugin.range_start_action.isChecked() is True

    plugin.on_last_frame_toggle()
    assert plugin._pending_end == Frame(99)
    assert plugin.range_end_action.isChecked() is True

    plugin.on_add_range_triggered()
    assert plugin.ranges_model.ranges[0][0].as_frames(plugin_controller.current_voutput) == (0, 99)

    # Exclusive mode: last frame is num_frames (100)
    plugin.settings.global_.exclusive = True
    plugin.on_first_frame_toggle()
    assert plugin._pending_start == Frame(0)

    plugin.on_last_frame_toggle()
    assert plugin._pending_end == Frame(100)

    plugin.on_add_range_triggered()
    assert plugin.ranges_model.ranges[1][0].as_frames(plugin_controller.current_voutput) == (0, 100)


def test_scening_timeline_synchronization_and_filtering(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    plugin_controller.set_current_frame(15)
    plugin.range_start_action.setChecked(True)
    plugin_controller.set_current_frame(35)
    plugin.range_end_action.setChecked(True)
    plugin.on_add_range_triggered()

    scene = plugin.scenes_model.scenes[0]
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 1

    # Toggle scene display off -> notches cleared
    plugin.scenes_model.setData(
        plugin.scenes_model.index(0, Col.DISPLAY), Qt.CheckState.Unchecked.value, ROLE_CHECK_STATE
    )
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 0

    # Toggle scene display on -> notches restored
    plugin.scenes_model.setData(
        plugin.scenes_model.index(0, Col.DISPLAY), Qt.CheckState.Checked.value, ROLE_CHECK_STATE
    )
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 1

    # Output filtering: exclude output index 0
    plugin.scenes_model.setData(plugin.scenes_model.index(0, Col.OUTPUTS), {1}, Qt.ItemDataRole.UserRole)
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 0

    # Output filtering: re-include output index 0
    plugin.scenes_model.setData(plugin.scenes_model.index(0, Col.OUTPUTS), {0, 1}, Qt.ItemDataRole.UserRole)
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 1

    # Delete scene -> completely cleans up notches
    plugin.scenes_model.remove_scene(0)
    assert len(plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])) == 0


def test_scening_multi_row_deletion_and_model_sync(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    # Add 3 ranges
    for start, end in [(10, 20), (30, 40), (50, 60)]:
        plugin_controller.set_current_frame(start)
        plugin.range_start_action.setChecked(True)
        plugin_controller.set_current_frame(end)
        plugin.range_end_action.setChecked(True)
        plugin.on_add_range_triggered()

    assert plugin.ranges_model.rowCount() == 3

    # Multi-select row 0 and row 2
    sel_model = plugin.ranges_view.selectionModel()
    sel_model.select(
        plugin.ranges_model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    sel_model.select(
        plugin.ranges_model.index(2, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )

    plugin.on_remove_range_triggered()

    # Verify only [30, 40] remains
    assert plugin.ranges_model.rowCount() == 1
    assert plugin.ranges_model.ranges[0][0].as_frames(plugin_controller.current_voutput) == (30, 40)
    assert len(plugin.scenes_model.scenes[0].ranges) == 1

    # Inline edit start frame via RangeTableModel.setData
    plugin.ranges_model.setData(plugin.ranges_model.index(0, RangeCol.START_FRAME), 25, Qt.ItemDataRole.EditRole)
    assert plugin.ranges_model.ranges[0][0].as_frames(plugin_controller.current_voutput) == (25, 40)

    # Timeline notch updated
    scene = plugin.scenes_model.scenes[0]
    notches = plugin_controller.timeline_proxy._notches.get(scene.notch_id, [])
    assert len(notches) == 1
    assert notches[0][0] == [(25, 40)]


def test_scening_clipboard_export_and_toolbar_cycle(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()

    for start, end in [(0, 24), (48, 72)]:
        plugin_controller.set_current_frame(start)
        plugin.range_start_action.setChecked(True)
        plugin_controller.set_current_frame(end)
        plugin.range_end_action.setChecked(True)
        plugin.on_add_range_triggered()

    # Select both rows
    sel_model = plugin.ranges_view.selectionModel()
    sel_model.select(
        plugin.ranges_model.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    sel_model.select(
        plugin.ranges_model.index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )

    plugin._copy_ranges_frames()
    assert QApplication.clipboard().text() == "[(0, 24), (48, 72)]"

    plugin._copy_ranges_timestamps()
    assert QApplication.clipboard().text() == '[("00:00:00.000", "00:00:01.000"), ("00:00:02.000", "00:00:03.000")]'

    # Toolbar style cycle
    initial_style = plugin.scenes_toolbar.toolButtonStyle()
    plugin.on_cycle_toolbar_style()
    new_style = plugin.scenes_toolbar.toolButtonStyle()
    assert new_style != initial_style
    assert plugin.range_toolbar.toolButtonStyle() == new_style
