from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QStyleOptionViewItem
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.api import Frame
from vsview.app.outputs.video import VideoOutput
from vsview.app.tools.scening.api import Serializer
from vsview.app.tools.scening.models import RangeFrame, RangeTime
from vsview.app.tools.scening.parsers import PythonListFramesParser, QPFileParser
from vsview.app.tools.scening.plugin import SceningPlugin
from vsview.app.tools.scening.serializer import PythonListFramesSerializer, PythonListTimestampsSerializer
from vsview.app.tools.scening.ui import ROLE_CHECK_STATE, Col, RangeCol
from vsview.app.views.timeline import FrameEdit

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
    create_test_video_output: Callable[[int], VideoOutput],
) -> HarnessPluginController:
    with (env := vpy_env_factory()).use():
        voutput = create_test_video_output(100)
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
    notches = plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])
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
    assert plugin.range_start_action.isChecked() is True

    plugin.on_last_frame_toggle()
    assert plugin.range_end_action.isChecked() is True

    plugin.on_add_range_triggered()
    assert plugin.ranges_model.ranges[0][0].as_frames(plugin_controller.current_voutput) == (0, 99)

    # Exclusive mode stores the same inclusive range; only the shown end frame is num_frames (100)
    plugin.settings.global_.exclusive = True
    plugin.on_first_frame_toggle()
    plugin.on_last_frame_toggle()

    plugin.on_add_range_triggered()
    assert plugin.ranges_model.ranges[1][0].as_frames(plugin_controller.current_voutput) == (0, 99)
    assert plugin.ranges_model.index(1, RangeCol.END_FRAME).data() == "100"


@pytest.mark.parametrize(
    ("exclusive", "expected"),
    [pytest.param(False, (10, 20), id="inclusive"), pytest.param(True, (10, 20), id="exclusive")],
)
def test_scening_mark_out_stores_the_marked_frame_as_the_last_frame(
    plugin_controller: HarnessPluginController, exclusive: bool, expected: tuple[int, int]
) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.settings.global_.exclusive = exclusive
    plugin.on_new_scene()

    plugin_controller.set_current_frame(10)
    plugin.range_start_action.setChecked(True)
    plugin_controller.set_current_frame(20)
    plugin.range_end_action.setChecked(True)
    plugin.on_add_range_triggered()

    assert plugin.scenes_model.scenes[0].ranges[0].to_tuple() == expected


def copy_from_range_menu(plugin: SceningPlugin, text: str) -> str:
    """Open the range table context menu, pick the action named `text`, and return the clipboard."""
    picked = list[str]()

    def pick() -> None:
        if not isinstance(menu := QApplication.activePopupWidget(), QMenu):
            return
        for action in menu.actions():
            if action.text() == text:
                action.trigger()
                picked.append(text)
        menu.close()

    QTimer.singleShot(0, pick)
    plugin.ranges_view.customContextMenuRequested.emit(QPoint(1, 1))
    assert picked == [text]
    return QApplication.clipboard().text()


def _single_range_plugin(
    plugin_controller: HarnessPluginController, exclusive: bool, r: RangeFrame | RangeTime
) -> SceningPlugin:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.settings.global_.exclusive = exclusive
    plugin.on_new_scene()
    plugin.ranges_model.add_range(r, plugin.scenes_model.scenes[0])
    return plugin


@pytest.mark.parametrize(
    ("exclusive", "end_frame"),
    [pytest.param(False, 20, id="inclusive"), pytest.param(True, 21, id="exclusive")],
)
def test_scening_range_table_shows_the_end_frame_in_the_chosen_convention(
    plugin_controller: HarnessPluginController, exclusive: bool, end_frame: int
) -> None:
    plugin = _single_range_plugin(plugin_controller, exclusive, RangeFrame(start=10, end=20))
    model = plugin.ranges_model

    cells = [model.index(0, col).data() for col in RangeCol]
    edit_end = model.index(0, RangeCol.END_FRAME).data(Qt.ItemDataRole.EditRole)

    assert cells == ["10", str(end_frame), "00:00:00.417", "00:00:00.833", ""]
    assert edit_end == end_frame


@pytest.mark.parametrize(
    ("exclusive", "typed", "stored"),
    [pytest.param(False, 30, 30, id="inclusive"), pytest.param(True, 30, 29, id="exclusive")],
)
def test_scening_range_table_stores_a_typed_end_frame_as_the_last_frame(
    plugin_controller: HarnessPluginController, exclusive: bool, typed: int, stored: int
) -> None:
    plugin = _single_range_plugin(plugin_controller, exclusive, RangeFrame(start=10, end=20))

    plugin.ranges_model.setData(plugin.ranges_model.index(0, RangeCol.END_FRAME), typed, Qt.ItemDataRole.EditRole)

    assert plugin.scenes_model.scenes[0].ranges[0].to_tuple() == (10, stored)


@pytest.mark.parametrize(
    ("exclusive", "end", "maximum"),
    [
        pytest.param(False, 99, 99, id="inclusive-last-frame"),
        pytest.param(True, 99, 100, id="exclusive-last-frame"),
        pytest.param(False, 150, 99, id="inclusive-past-the-clip"),
        pytest.param(True, 150, 100, id="exclusive-past-the-clip"),
    ],
)
def test_scening_end_frame_editor_does_not_change_an_untouched_end(
    plugin_controller: HarnessPluginController, exclusive: bool, end: int, maximum: int
) -> None:
    plugin = _single_range_plugin(plugin_controller, exclusive, RangeFrame(start=50, end=end))
    index = plugin.ranges_model.index(0, RangeCol.END_FRAME)
    modified = list[None]()
    plugin.ranges_model.rangesModified.connect(lambda: modified.append(None))
    editor = plugin.ranges_delegate.createEditor(plugin.ranges_view.viewport(), QStyleOptionViewItem(), index)
    assert isinstance(editor, FrameEdit)

    plugin.ranges_delegate.setEditorData(editor, index)
    plugin.ranges_delegate.setModelData(editor, plugin.ranges_model, index)

    assert editor.maximum() == maximum
    assert plugin.scenes_model.scenes[0].ranges[0].to_tuple() == (50, end)
    assert modified == []


def test_scening_end_frame_editor_writes_a_changed_end(plugin_controller: HarnessPluginController) -> None:
    plugin = _single_range_plugin(plugin_controller, True, RangeFrame(start=50, end=60))
    index = plugin.ranges_model.index(0, RangeCol.END_FRAME)
    editor = plugin.ranges_delegate.createEditor(plugin.ranges_view.viewport(), QStyleOptionViewItem(), index)
    assert isinstance(editor, FrameEdit)
    plugin.ranges_delegate.setEditorData(editor, index)

    editor.setValue(100)
    plugin.ranges_delegate.setModelData(editor, plugin.ranges_model, index)

    assert plugin.scenes_model.scenes[0].ranges[0].to_tuple() == (50, 99)


def test_scening_range_table_refreshes_when_the_settings_change(
    qtbot: QtBot, plugin_controller: HarnessPluginController
) -> None:
    plugin = _single_range_plugin(plugin_controller, False, RangeFrame(start=10, end=20))

    with qtbot.waitSignal(plugin.ranges_model.dataChanged, timeout=1000):
        plugin_controller.globalSettingsChanged.emit()


@pytest.mark.parametrize(
    ("exclusive", "frames", "end_frames"),
    [
        pytest.param(False, "[(0, 24), (48, 48)]", "[24, 48]", id="inclusive"),
        pytest.param(True, "[(0, 25), (48, 49)]", "[25, 49]", id="exclusive"),
    ],
)
def test_scening_copy_uses_the_chosen_end_convention(
    plugin_controller: HarnessPluginController, exclusive: bool, frames: str, end_frames: str
) -> None:
    plugin = _single_range_plugin(plugin_controller, exclusive, RangeFrame(start=0, end=24))
    plugin.ranges_model.add_range(RangeFrame(start=48), plugin.scenes_model.scenes[0])
    plugin.ranges_view.selectAll()

    plugin.copy_frames_action.trigger()
    copied_frames = QApplication.clipboard().text()
    copied_end_frames = copy_from_range_menu(plugin, "Copy end frames only")
    copied_timestamps = copy_from_range_menu(plugin, "Copy timestamps")

    assert copied_frames == frames
    assert copied_end_frames == end_frames
    assert copied_timestamps == '[("00:00:00.000", "00:00:01.000"), ("00:00:02.000", "00:00:02.000")]'


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
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 1

    # Toggle scene display off -> notches cleared
    plugin.scenes_model.setData(
        plugin.scenes_model.index(0, Col.DISPLAY), Qt.CheckState.Unchecked.value, ROLE_CHECK_STATE
    )
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 0

    # Toggle scene display on -> notches restored
    plugin.scenes_model.setData(
        plugin.scenes_model.index(0, Col.DISPLAY), Qt.CheckState.Checked.value, ROLE_CHECK_STATE
    )
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 1

    # Output filtering: exclude output index 0
    plugin.scenes_model.setData(plugin.scenes_model.index(0, Col.OUTPUTS), {1}, Qt.ItemDataRole.UserRole)
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 0

    # Output filtering: re-include output index 0
    plugin.scenes_model.setData(plugin.scenes_model.index(0, Col.OUTPUTS), {0, 1}, Qt.ItemDataRole.UserRole)
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 1

    # Delete scene -> completely cleans up notches
    plugin.scenes_model.remove_scene(0)
    assert len(plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])) == 0


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
    notches = plugin_controller.timeline_proxy.testing._notches.get(scene.notch_id, [])
    assert len(notches) == 1
    assert notches[0][0] == [(25, 40)]


def test_scening_deleting_ranges_of_several_scenes_saves_once(plugin_controller: HarnessPluginController) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    plugin.on_new_scene()
    plugin.on_new_scene()
    first, second = plugin.scenes_model.scenes
    for (start, end), scene in [((10, 20), first), ((30, 40), first), ((50, 60), second), ((70, 80), second)]:
        plugin.ranges_model.add_range(RangeFrame(start=start, end=end), scene)
    modified = list[None]()
    plugin.ranges_model.rangesModified.connect(lambda: modified.append(None))
    sel_model = plugin.ranges_view.selectionModel()
    for row in (0, 1, 3):
        sel_model.select(
            plugin.ranges_model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )

    plugin.on_remove_range_triggered()

    assert len(modified) == 1
    assert [[r.to_tuple() for r in scene.ranges] for scene in plugin.settings.local_.scenes] == [[], [(50, 60)]]
    assert [r.to_tuple() for r, _ in plugin.ranges_model.ranges] == [(50, 60)]


def test_scening_clipboard_export_and_toolbar_cycle(
    plugin_controller: HarnessPluginController,
) -> None:
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

    plugin.copy_frames_action.trigger()
    assert QApplication.clipboard().text() == "[(0, 24), (48, 72)]"

    copied_timestamps = copy_from_range_menu(plugin, "Copy timestamps")
    assert copied_timestamps == '[("00:00:00.000", "00:00:01.000"), ("00:00:02.000", "00:00:03.000")]'

    # Toolbar style cycle
    initial_style = plugin.scenes_toolbar.toolButtonStyle()
    plugin.on_cycle_toolbar_style()
    new_style = plugin.scenes_toolbar.toolButtonStyle()
    assert new_style != initial_style
    assert plugin.range_toolbar.toolButtonStyle() == new_style


# Import & export
def test_parse_imported_files_sorts_files_into_scenes_failures_and_empty_files(
    plugin_controller: HarnessPluginController, tmp_path: Path
) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    good, empty, missing = tmp_path / "good.qp", tmp_path / "empty.qp", tmp_path / "missing.qp"
    good.write_text("10 I\n20 K\n", encoding="utf-8")
    empty.write_text("", encoding="utf-8")

    scenes, failed, without_ranges = plugin.parse_imported_files(
        [str(good), str(empty), str(missing)], QPFileParser()
    ).result(timeout=5)

    assert [(s.name, [r.to_tuple() for r in s.ranges]) for s in scenes] == [("good", [(10, 10), (20, 20)])]
    assert failed == [str(missing)]
    assert without_ranges == [str(empty)]


@pytest.mark.parametrize(("end_offset", "expected"), [(0, (10, 21)), (1, (10, 20))], ids=["inclusive", "exclusive"])
def test_parse_imported_frame_lists_convert_exclusive_ends(
    plugin_controller: HarnessPluginController, tmp_path: Path, end_offset: int, expected: tuple[int, int]
) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    frames = tmp_path / "frames.txt"
    frames.write_text("[(10, 21)]", encoding="utf-8")

    scenes, _, _ = plugin.parse_imported_files([str(frames)], PythonListFramesParser(), end_offset).result(timeout=5)

    assert [r.to_tuple() for r in scenes[0].ranges] == [expected]


def test_import_scene_adds_the_parsed_scenes(
    plugin_controller: HarnessPluginController, qtbot: QtBot, mocker: MockerFixture, tmp_path: Path
) -> None:
    plugin = plugin_controller.create_plugin(SceningPlugin)
    plugin_controller.on_workspace_loaded()
    qp_file = tmp_path / "chapters.qp"
    qp_file.write_text("10 I\n20 K\n", encoding="utf-8")
    label = QPFileParser.filter.label
    mocker.patch.object(QFileDialog, "getOpenFileNames", return_value=([str(qp_file)], f"{label} (*.qp *.txt)"))

    with qtbot.waitSignal(plugin_controller.statusMessage, timeout=5000):
        plugin.on_import_scene()

    assert [s.name for s in plugin.scenes_model.scenes] == ["chapters"]


@pytest.mark.parametrize(
    ("serializer", "end_offset", "expected"),
    [
        (PythonListFramesSerializer(), 0, "[(10, 20)]"),
        (PythonListFramesSerializer(), 1, "[(10, 21)]"),
        (PythonListTimestampsSerializer(), 1, "[('00:00:00.417', '00:00:00.833')]"),
    ],
    ids=["frames_inclusive", "frames_exclusive", "timestamps_ignore_the_offset"],
)
def test_serialize_exported_scene_writes_the_selected_scene(
    plugin_controller: HarnessPluginController,
    qtbot: QtBot,
    tmp_path: Path,
    serializer: Serializer,
    end_offset: int,
    expected: str,
) -> None:
    plugin = _single_range_plugin(plugin_controller, False, RangeFrame(start=10, end=20))
    plugin.scenes_view.selectRow(0)
    target = tmp_path / "export.txt"

    fut = plugin.serialize_exported_scene(str(target), serializer, end_offset)
    qtbot.waitUntil(fut.done, timeout=5000)
    fut.result()

    assert target.read_text(encoding="utf-8") == expected
