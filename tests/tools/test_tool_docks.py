from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
import vapoursynth as vs
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.app.outputs.video import VideoOutput
from vsview.app.tools.colorpicker.plugin import ColorPickerPlugin, TrackingState
from vsview.app.tools.cropping.plugin import RegionSelectorPlugin
from vsview.app.tools.frameprops.plugin import FramePropsModel, FramePropsPlugin

if TYPE_CHECKING:
    from tests.harness import PluginTestHarness

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]


@pytest.fixture(autouse=True)
def _use_qt_event_loop(qt_event_loop: Any) -> Any:
    return qt_event_loop


@pytest.fixture
def plugin_harness(
    plugin_harness: PluginTestHarness,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    create_test_video_output: Callable[[ManagedEnvironment, int], VideoOutput],
) -> PluginTestHarness:
    env = vpy_env_factory()
    voutput = create_test_video_output(env, 100)
    plugin_harness.env = env
    plugin_harness.set_current_voutput(voutput)
    return plugin_harness


# RegionSelectorPlugin Tests
def test_region_selector_instantiation(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(RegionSelectorPlugin)
    assert plugin.api is plugin_harness
    assert plugin.crop_rect.isEmpty()
    assert plugin.enable_btn.isChecked() is False
    assert plugin_harness.current_view.rect_selection_enabled is False

    assert plugin.left_box.value() == 0
    assert plugin.right_box.value() == 0
    assert plugin.top_box.value() == 0
    assert plugin.bottom_box.value() == 0
    assert plugin.width_box.value() == 0
    assert plugin.height_box.value() == 0


def test_region_selector_selection_update_and_mod_clamping(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(RegionSelectorPlugin)

    # 1. Even coordinates on 64x64 video (already mod 2 aligned)
    aligned_rect = QRect(10, 10, 30, 30)
    plugin_harness.set_rect_selection(aligned_rect)

    assert plugin.crop_rect == aligned_rect
    assert plugin.left_box.value() == 10
    assert plugin.top_box.value() == 10
    assert plugin.width_box.value() == 30
    assert plugin.height_box.value() == 30
    assert plugin.right_box.value() == 24  # 64 - (10 + 30)
    assert plugin.bottom_box.value() == 24  # 64 - (10 + 30)

    # Also verify spinbox direct references
    assert plugin.left_spin.value() == 10
    assert plugin.top_spin.value() == 10
    assert plugin.width_spin.value() == 30
    assert plugin.height_spin.value() == 30

    # 2. Odd coordinates requiring modulus clamping (mod 2)
    # x=9 -> x0=8, y=7 -> y0=6, x+w=40 -> x1=40, y+h=32 -> y1=32
    # w=32, h=26
    unaligned_rect = QRect(9, 7, 31, 25)
    plugin_harness.set_rect_selection(unaligned_rect)

    expected_clamped = QRect(8, 6, 32, 26)
    assert plugin.crop_rect == expected_clamped
    assert plugin.left_box.value() == 8
    assert plugin.top_box.value() == 6
    assert plugin.width_box.value() == 32
    assert plugin.height_box.value() == 26
    assert plugin.right_box.value() == 24  # 64 - 40
    assert plugin.bottom_box.value() == 32  # 64 - 32


def test_region_selector_aspect_ratio_and_command_labels(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(RegionSelectorPlugin)

    # Before selection, command label shows unformatted template
    assert plugin.command_label.text() == "std.Crop({left}, {right}, {top}, {bottom})"

    # Set selection and verify crop script label formatting
    test_rect = QRect(10, 10, 30, 30)
    plugin_harness.set_rect_selection(test_rect)

    assert plugin.command_label.text() == "std.Crop(10, 24, 10, 24)"
    assert len(plugin.cmd_labels) == 3
    assert plugin.cmd_labels[0].text() == "std.Crop(10, 24, 10, 24)"
    assert plugin.cmd_labels[1].text() == "std.CropRel(10, 24, 10, 24)"
    assert plugin.cmd_labels[2].text() == "std.CropAbs(30, 30, 10, 10)"

    # Simulate 16:9 aspect ratio preset calculation (32x18 on 64x64)
    rect_16_9 = QRect(16, 22, 32, 18)
    plugin_harness.set_rect_selection(rect_16_9)
    assert plugin.command_label.text() == "std.Crop(16, 16, 22, 24)"

    # Simulate 4:3 aspect ratio preset calculation (32x24 on 64x64)
    rect_4_3 = QRect(16, 20, 32, 24)
    plugin_harness.set_rect_selection(rect_4_3)
    assert plugin.command_label.text() == "std.Crop(16, 16, 20, 20)"

    # Clear selection resets command labels
    plugin.clear_btn.click()
    assert plugin.crop_rect.isEmpty()
    assert plugin.command_label.text() == "std.Crop({left}, {right}, {top}, {bottom})"


def test_region_selector_toggle_action_and_shortcut(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(RegionSelectorPlugin)

    # Initial state
    assert plugin.enable_btn.isChecked() is False
    assert plugin_harness.current_view.rect_selection_enabled is False

    # Toggle via button click
    plugin.enable_btn.click()
    assert plugin.enable_btn.isChecked() is True
    assert plugin_harness.current_view.rect_selection_enabled is True

    plugin.enable_btn.click()
    assert plugin.enable_btn.isChecked() is False
    assert plugin_harness.current_view.rect_selection_enabled is False

    # Toggle via button toggle
    plugin.enable_btn.toggle()
    assert plugin.enable_btn.isChecked() is True
    assert plugin_harness.current_view.rect_selection_enabled is True

    # on_hide turns off selection
    plugin.on_hide()
    assert plugin.enable_btn.isChecked() is False
    assert plugin_harness.current_view.rect_selection_enabled is False


def test_region_selector_spinbox_edits_update_view(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(RegionSelectorPlugin)

    # Set initial selection
    plugin_harness.set_rect_selection(QRect(10, 10, 30, 30))

    # Edit left_spin: value changed triggers view rect selection update
    plugin.left_spin.setValue(12)
    assert plugin_harness.current_view.rect_selection.x() == 12
    assert plugin.left_box.value() == 12


# ColorPickerPlugin Tests
def test_color_picker_instantiation(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(ColorPickerPlugin)
    assert plugin.tracking == TrackingState.INACTIVE
    assert plugin.eyedropper_btn.isChecked() is False
    assert plugin.position_label.text() == "Pos: —, —"
    assert plugin.position_label.cursor_pos == QPoint(0, 0)


def test_color_picker_tracking_state_transitions(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(ColorPickerPlugin)

    # Inactive -> Active via eyedropper button
    plugin.eyedropper_btn.click()
    assert plugin.tracking == TrackingState.ACTIVE
    assert plugin.eyedropper_btn.isChecked() is True

    assert plugin_harness.current_view.viewport.cursor == Qt.CursorShape.CrossCursor

    # Active -> Deactivating via Right Mouse Button Press
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    plugin.on_view_mouse_pressed(press_event)
    assert plugin.tracking == TrackingState.DEACTIVATING
    assert plugin_harness.current_view.viewport.cursor == Qt.CursorShape.OpenHandCursor

    # Deactivating -> Inactive via Context Menu Event
    context_event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(10, 10), QPoint(10, 10))
    plugin.on_view_context_menu(context_event)
    assert plugin.tracking == TrackingState.INACTIVE
    assert plugin.eyedropper_btn.isChecked() is False


def test_color_picker_mouse_move_and_position_label(qtbot: QtBot, plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(ColorPickerPlugin)
    plugin.eyedropper_btn.setChecked(True)

    # Simulate mouse movement to (15, 25)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(15, 25),
        QPointF(15, 25),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    plugin.on_view_mouse_moved(move_event)
    qtbot.waitUntil(lambda: plugin.position_label.cursor_pos == QPoint(15, 25))

    assert plugin.position_label.cursor_pos == QPoint(15, 25)
    assert plugin.position_label.text() == "Pos: 15, 25"

    # Verify black frame source and rendered values
    assert plugin.src_labels["Dec"][0].text().strip() == "0"
    assert plugin.src_labels["Hex"][0].text().strip() == "0"
    assert plugin.src_labels["Norm"][0].text() == f"{0.0:.5f}"

    assert plugin.rgb_labels["Dec"][0].text() == "0"
    assert plugin.rgb_labels["Hex"][0].text() == "00"
    assert plugin.rgb_labels["Norm"][0].text() == f"{0.0:.5f}"
    assert plugin.rgb_labels["HLS"][0].text() == "0°"
    assert plugin.rgb_labels["HSV"][0].text() == "0°"


def test_color_picker_decimals_nb_setting_update(qtbot: QtBot, plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(ColorPickerPlugin)
    plugin.eyedropper_btn.setChecked(True)

    # Trigger initial update
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    plugin.on_view_mouse_moved(move_event)
    qtbot.waitUntil(lambda: plugin.position_label.cursor_pos == QPoint(10, 10))

    # Verify default 5 decimals
    assert plugin.settings.global_.decimals_nb == 5
    assert plugin.src_labels["Norm"][0].text() == "0.00000"
    assert plugin.rgb_labels["Norm"][0].text() == "0.00000"

    # Update decimals_nb to 2 via public plugin contract
    plugin.update_global_settings(decimals_nb=2)
    qtbot.waitUntil(lambda: plugin.rgb_labels["Norm"][0].text() == "0.00")

    assert plugin.settings.global_.decimals_nb == 2
    assert plugin.src_labels["Norm"][0].text() == "0.00"
    assert plugin.rgb_labels["Norm"][0].text() == "0.00"

    # Update decimals_nb to 4 via public plugin contract
    plugin.update_global_settings(decimals_nb=4)
    qtbot.waitUntil(lambda: plugin.rgb_labels["Norm"][0].text() == "0.0000")

    assert plugin.settings.global_.decimals_nb == 4
    assert plugin.src_labels["Norm"][0].text() == "0.0000"
    assert plugin.rgb_labels["Norm"][0].text() == "0.0000"


def test_color_picker_copy_row(qtbot: QtBot, plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(ColorPickerPlugin)
    plugin.eyedropper_btn.setChecked(True)

    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    plugin.on_view_mouse_moved(move_event)
    qtbot.waitUntil(lambda: plugin.position_label.cursor_pos == QPoint(5, 5))

    status_messages = list[str]()
    plugin_harness.statusMessage.connect(status_messages.append)

    # Click copy button via user interaction with qtbot
    copy_btn = plugin.src_copy_btns["Dec"]
    qtbot.mouseClick(copy_btn, Qt.MouseButton.LeftButton)

    assert QApplication.clipboard().text() == "0  , 0  , 0  "
    assert len(status_messages) == 1
    assert "Copied '0  , 0  , 0  ' to clipboard" in status_messages[0]


# FramePropsPlugin Tests
def test_frameprops_instantiation(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)
    assert plugin.api is plugin_harness
    assert plugin.categorize_toggle.isChecked() is True
    assert plugin.stack.currentIndex() == 1  # categorize_tree
    assert isinstance(plugin.search_box, QLineEdit)
    assert plugin.search_box.placeholderText() == "Filter properties..."
    assert plugin.search_box.isClearButtonEnabled() is True


def test_frameprops_load_properties_on_frame_change(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)

    # Load frame 0 props
    plugin_harness.set_current_frame(0)

    assert plugin.history_combo.count() == 1
    assert "Frame 0 (Current)" in plugin.history_combo.currentText()

    # Raw table inspection
    table_model = plugin.raw_table.model()
    raw_keys = [str(table_model.index(r, 0).data()) for r in range(table_model.rowCount())]
    assert "_Matrix" in raw_keys
    assert "_Primaries" in raw_keys
    assert "_Transfer" in raw_keys

    # Categorize tree inspection
    tree_model = plugin.categorize_tree.current_model
    category_names = [str(tree_model.index(r, 0).data()) for r in range(tree_model.rowCount())]
    assert "Video" in category_names

    # Check formatted values under Video category
    video_item = tree_model.category_items["Video"]
    video_props = {video_item.child(r, 0).text(): video_item.child(r, 2).text() for r in range(video_item.rowCount())}
    assert video_props.get("_Primaries") == "BT.709"
    assert video_props.get("_Transfer") == "BT.709"

    # Move to frame 1
    plugin_harness.set_current_frame(1)
    assert plugin.history_combo.count() == 2
    assert "Frame 1 (Current)" in plugin.history_combo.currentText()


def test_frameprops_search_filter_box(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)
    plugin_harness.set_current_frame(0)

    table = plugin.raw_table
    model = table.model()
    total_rows = model.rowCount()
    assert total_rows == 5

    # Initially, no rows hidden
    assert all(not table.isRowHidden(r) for r in range(total_rows))

    # Filter for "_Matrix"
    plugin.search_box.setText("_Matrix")

    matrix_rows = [r for r in range(total_rows) if str(model.index(r, 0).data()) == "_Matrix"]
    other_rows = [r for r in range(total_rows) if str(model.index(r, 0).data()) != "_Matrix"]

    assert len(matrix_rows) == 1
    assert not table.isRowHidden(matrix_rows[0])
    assert all(table.isRowHidden(r) for r in other_rows)

    # Filter with case-insensitivity: "matrix"
    plugin.search_box.setText("matrix")
    assert not table.isRowHidden(matrix_rows[0])
    assert all(table.isRowHidden(r) for r in other_rows)

    # Also check categorized tree filtering
    tree = plugin.categorize_tree
    tree_model = tree.current_model
    video_item = tree_model.category_items["Video"]
    video_cat_index = tree_model.indexFromItem(video_item)

    child_keys = [video_item.child(r, 0).text() for r in range(video_item.rowCount())]
    for r, k in enumerate(child_keys):
        if "matrix" in k.lower():
            assert not tree.isRowHidden(r, video_cat_index)
        else:
            assert tree.isRowHidden(r, video_cat_index)

    # Clear filter restores visibility
    plugin.search_box.clear()
    assert all(not table.isRowHidden(r) for r in range(total_rows))
    assert all(not tree.isRowHidden(r, video_cat_index) for r in range(video_item.rowCount()))


def test_frameprops_categorize_and_format_toggles(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)
    plugin_harness.set_current_frame(0)

    # Categorize toggle
    assert plugin.categorize_toggle.isChecked() is True
    assert plugin.stack.currentIndex() == 1  # categorize_tree

    plugin.categorize_toggle.setChecked(False)
    assert plugin.stack.currentIndex() == 0  # raw_table
    assert plugin.formatted_toggle.isEnabled() is False

    plugin.categorize_toggle.setChecked(True)
    assert plugin.stack.currentIndex() == 1  # categorize_tree
    assert plugin.formatted_toggle.isEnabled() is True

    # Format toggle shows/hides Formatted column in tree view
    plugin.formatted_toggle.setChecked(True)
    assert plugin.categorize_tree.header().isSectionHidden(FramePropsModel.FORMATTED_COLUMN) is False

    plugin.formatted_toggle.setChecked(False)
    assert plugin.categorize_tree.header().isSectionHidden(FramePropsModel.FORMATTED_COLUMN) is True


def test_frameprops_history_navigation_buttons(plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)

    # Populate frames 0, 1, 2 in history
    plugin_harness.set_current_frame(0)
    plugin_harness.set_current_frame(1)
    plugin_harness.set_current_frame(2)

    assert plugin.history_combo.count() == 3
    assert plugin.history_combo.currentIndex() == 2
    assert plugin.prev_btn.isEnabled() is True
    assert plugin.next_btn.isEnabled() is False

    # Navigate backward using prev_btn
    plugin.prev_btn.click()
    assert plugin.history_combo.currentIndex() == 1
    assert plugin.prev_btn.isEnabled() is True
    assert plugin.next_btn.isEnabled() is True

    plugin.prev_btn.click()
    assert plugin.history_combo.currentIndex() == 0
    assert plugin.prev_btn.isEnabled() is False
    assert plugin.next_btn.isEnabled() is True

    # Navigate forward using next_btn
    plugin.next_btn.click()
    assert plugin.history_combo.currentIndex() == 1


def test_frameprops_videoframe_preview_lifecycle(qtbot: QtBot, plugin_harness: PluginTestHarness) -> None:
    plugin = plugin_harness.create_plugin(FramePropsPlugin)
    plugin_harness.set_current_frame(0)

    # Create dummy VideoFrame property
    assert plugin_harness.env is not None
    with plugin_harness.env.use():
        dummy_clip = plugin_harness.env.core.std.BlankClip(width=32, height=32, format=vs.RGB24, length=1)
        dummy_frame = dummy_clip.get_frame(0)

    # Show preview via public signal contract
    plugin.raw_table.previewRequested.emit(dummy_frame, "_AlphaFrame")
    assert plugin.current_preview_key == "_AlphaFrame"
    assert plugin.preview_label.text() == "Preview: '_AlphaFrame'"
    assert plugin.splitter.sizes()[1] > 0

    # Hide preview via close button user click
    close_btns = [b for b in plugin.preview_container.findChildren(QToolButton) if b.toolTip() == "Close preview"]
    assert len(close_btns) == 1
    close_btn = close_btns[0]
    close_btn.click()

    qtbot.waitUntil(lambda: plugin.splitter.sizes()[1] == 0)
    assert plugin.splitter.sizes()[1] == 0
    assert not hasattr(plugin, "current_preview_key")
