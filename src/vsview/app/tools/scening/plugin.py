from __future__ import annotations

from bisect import bisect_left, bisect_right
from enum import StrEnum
from functools import cache
from itertools import count, cycle, islice
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Self, override

import pluggy
from jetpytools import cachedproperty, classproperty, flatten, to_arr
from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHeaderView,
    QMenu,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from vsview.api import (
    ActionDefinition,
    Dropdown,
    Frame,
    IconName,
    IconReloadMixin,
    LocalSettingsModel,
    PluginAPI,
    Time,
    VideoOutputProxy,
    WidgetPluginBase,
    run_in_background,
)

from . import specs
from .constants import PLUGIN_DISPLAY_NAME, PLUGIN_IDENTIFIER
from .models import RangeFrame, RangeTime, SceneRow, UnifiedRange
from .parsers import internal_parsers
from .serializer import internal_serializers
from .ui import Col, RangeCol, RangeTableDelegate, RangeTableModel, SceneTableDelegate, SceneTableModel
from .utils import ColorGenerator, monkey_patch_parser

if TYPE_CHECKING:
    from .api import Parser, Serializer

logger = getLogger(__name__)


manager = pluggy.PluginManager("vsview.scening")


@cache
def load_plugins() -> None:
    manager.add_hookspecs(specs)
    n = manager.load_setuptools_entrypoints("vsview.scening")
    logger.debug("Loaded %d plugins", n)


class ShortcutDefinition(StrEnum):
    definition: ActionDefinition

    CYCLE_TOOLBAR_STYLE = "cycle_toolbar_style", "Cycle toolbar style", ""

    REMOVE_SCENE = "remove_scene", "Remove selected scene", "Delete"

    TOGGLE_RANGE_START = "toggle_range_start", "Toggle Range Start", "Q"
    TOGGLE_RANGE_END = "toggle_range_end", "Toggle Range End", "W"
    VALIDATE_RANGE = "validate_range", "Validate selected range", "E"
    ADD_SINGLE_FRAME = "add_single_frame", "Add Single Frame", "R"
    REMOVE_RANGE = "remove_range", "Remove selected range", "Delete"

    SEEK_PREV_BOUND = "seek_prev_bound", "Seek to previous range boundary", "Ctrl+Left"
    SEEK_NEXT_BOUND = "seek_next_bound", "Seek to next range boundary", "Ctrl+Right"
    SEEK_PREV_RANGE = "seek_prev_range", "Seek to previous range", "Ctrl+Shift+Left"
    SEEK_NEXT_RANGE = "seek_next_range", "Seek to next range", "Ctrl+Shift+Right"

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(f"{PLUGIN_IDENTIFIER}.{value}", label, default_key)
        return obj


class GlobalSettings(BaseModel):
    model_config = ConfigDict(ignored_types=(classproperty,))

    toolbar_style: Annotated[
        Qt.ToolButtonStyle,
        Dropdown(
            label="Toolbar Style",
            items=[
                ("Icon Only", Qt.ToolButtonStyle.ToolButtonIconOnly),
                ("Text Only", Qt.ToolButtonStyle.ToolButtonTextOnly),
                ("Text Beside Icon", Qt.ToolButtonStyle.ToolButtonTextBesideIcon),
                ("Text Under Icon", Qt.ToolButtonStyle.ToolButtonTextUnderIcon),
            ],
            tooltip="Select the style for the toolbars.",
        ),
    ] = Qt.ToolButtonStyle.ToolButtonTextBesideIcon

    last_selected_parser: str = ""

    @classproperty
    @classmethod
    def toolbar_styles(cls) -> cycle[Qt.ToolButtonStyle]:
        return cycle(list(Qt.ToolButtonStyle)[:-1])


class LocalSettings(LocalSettingsModel):
    scenes: list[SceneRow] = Field(default_factory=list)


class SceningPlugin(WidgetPluginBase[GlobalSettings, LocalSettings], IconReloadMixin):
    identifier = PLUGIN_IDENTIFIER
    display_name = PLUGIN_DISPLAY_NAME

    shortcuts = tuple(s.definition for s in ShortcutDefinition)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        IconReloadMixin.__init__(self)

        self.output_map = dict[int, str]()
        self._pending_start: Frame | Time | None = None
        self._pending_end: Frame | Time | None = None

        self.setup_ui()
        self.setup_shortcuts()
        self.load_settings()

        self.register_icon_callback(self.on_reload_icon)

        self.api.globalSettingsChanged.connect(self._update_toolbar_style)

        self._update_toolbar_style()
        self._update_action_labels()

    def setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(self, orientation=Qt.Orientation.Vertical)
        layout.addWidget(self.splitter)

        self.scenes_container = QWidget(self.splitter)
        scenes_layout = QVBoxLayout(self.scenes_container)
        scenes_layout.setContentsMargins(0, 0, 0, 0)
        scenes_layout.setSpacing(0)

        # Top toolbar
        self.scenes_toolbar = QToolBar(self.scenes_container, movable=False)
        scenes_layout.addWidget(self.scenes_toolbar)

        self.new_scene_action = self.make_action(IconName.PLUS, "Create a new scene", self.scenes_container)
        self.new_scene_action.setText("Create new scene")
        self.new_scene_action.triggered.connect(self.on_new_scene)
        self.scenes_toolbar.addAction(self.new_scene_action)

        self.import_scene_action = self.make_action(
            IconName.FILE_IMPORT,
            "Import a file suitable for scening",
            self.scenes_container,
        )
        self.import_scene_action.setText("Import scene...")
        self.import_scene_action.triggered.connect(self.on_import_scene)
        self.scenes_toolbar.addAction(self.import_scene_action)

        self.export_scene_action = self.make_action(
            IconName.FILE_EXPORT,
            "Export a scene to a supported format",
            self.scenes_container,
        )
        self.export_scene_action.setText("Export scene...")
        self.export_scene_action.setDisabled(True)
        self.export_scene_action.triggered.connect(self.on_export_scene)
        self.scenes_toolbar.addAction(self.export_scene_action)

        # Scenes model + delegate
        self.scenes_model = SceneTableModel(self.output_map, self.scenes_container)
        self.scenes_model.scenesModified.connect(self._persist_scenes)
        self.scenes_model.sceneDisplayModified.connect(self._refresh_scene_on_timeline)
        self.scenes_model.sceneColorModified.connect(self._refresh_scene_on_timeline)
        self.scenes_model.sceneCheckOutputsModified.connect(self._refresh_scene_on_timeline)
        self.scenes_delegate = SceneTableDelegate(self.output_map, self.scenes_container)

        self.scenes_view = QTableView(self.scenes_container, showGrid=False)
        self.scenes_view.setAlternatingRowColors(False)
        self.scenes_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.scenes_view.setSortingEnabled(False)
        self.scenes_view.verticalHeader().setVisible(False)
        self.scenes_view.setModel(self.scenes_model)
        self.scenes_view.setItemDelegate(self.scenes_delegate)
        self.scenes_view.selectionModel().selectionChanged.connect(self.on_scene_selection_changed)

        # Scenes column sizing
        header = self.scenes_view.horizontalHeader()
        header.setSectionResizeMode(Col.COLOR, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(Col.COLOR, 40)
        header.setSectionResizeMode(Col.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Col.OUTPUTS, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Col.DISPLAY, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(Col.DISPLAY, 55)
        header.setSectionResizeMode(Col.DELETE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(Col.DELETE, 30)

        scenes_layout.addWidget(self.scenes_view)
        self.splitter.addWidget(self.scenes_container)

        self.range_container = QWidget(self.splitter)
        self.range_container.setDisabled(True)
        range_layout = QVBoxLayout(self.range_container)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(0)

        # Range toolbar
        self.range_toolbar = QToolBar(self.range_container, movable=False)
        range_layout.addWidget(self.range_toolbar)

        self.range_start_action = self.make_action(
            IconName.MARK_IN,
            "Toggle start of new range",
            self.range_container,
            checkable=True,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.range_start_action.setIconText("Mark in")
        self.range_start_action.toggled.connect(self.on_range_start_toggle)

        self.range_end_action = self.make_action(
            IconName.MARK_OUT,
            "Toggle end of new range",
            self.range_container,
            checkable=True,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.range_end_action.setIconText("Mark out")
        self.range_end_action.toggled.connect(self.on_range_end_toggle)

        self.add_range_action = self.make_action(
            IconName.SCENE_ADD,
            "Add current selected range",
            self.range_container,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.add_range_action.setIconText("Add range")
        self.add_range_action.setDisabled(True)
        self.add_range_action.triggered.connect(self.on_add_range_triggered)

        self.add_frame_action = self.make_action(
            IconName.FRAME_ADD,
            "Add current selected frame as range without end boundary",
            self.range_container,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.add_frame_action.setIconText("Add frame")
        self.add_frame_action.triggered.connect(self.on_add_frame_triggered)

        self.range_toolbar.addActions(
            [
                self.range_start_action,
                self.range_end_action,
                self.add_range_action,
                self.add_frame_action,
            ]
        )

        self.ranges_model = RangeTableModel(self.range_container, self.api)
        self.ranges_model.rangesModified.connect(self._persist_scenes)
        self.ranges_model.rangeDataModified.connect(self._refresh_range_on_timeline)
        self.ranges_model.modelReset.connect(self._update_ranges_header_width)
        self.ranges_model.rowsInserted.connect(self._update_ranges_header_width)
        self.ranges_model.rowsRemoved.connect(self._update_ranges_header_width)

        self.ranges_delegate = RangeTableDelegate(self.range_container)

        self.ranges_view = QTableView(self.range_container)
        self.ranges_view.setAlternatingRowColors(True)
        self.ranges_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ranges_view.setSortingEnabled(True)
        self.ranges_view.verticalHeader().setVisible(True)
        self.ranges_view.setModel(self.ranges_model)
        self.ranges_view.setItemDelegate(self.ranges_delegate)
        self.ranges_view.selectionModel().selectionChanged.connect(self.on_range_selection_changed)

        # Context menu & Ctrl+C
        self.ranges_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ranges_view.customContextMenuRequested.connect(self._on_ranges_context_menu)

        self.copy_frames_action = QAction(
            "Copy frames",
            self.ranges_view,
            shortcut=QKeySequence(QKeySequence.StandardKey.Copy),
            shortcutContext=Qt.ShortcutContext.WidgetShortcut,
        )
        self.copy_frames_action.triggered.connect(self._copy_ranges_frames)

        self.remove_range_action = QAction("Remove selected range", self.ranges_view)
        self.remove_range_action.triggered.connect(self.on_remove_range_triggered)

        self.ranges_view.addActions([self.copy_frames_action, self.remove_range_action])

        r_header = self.ranges_view.horizontalHeader()
        r_header.setSectionResizeMode(RangeCol.START_FRAME, QHeaderView.ResizeMode.Interactive)
        r_header.setSectionResizeMode(RangeCol.END_FRAME, QHeaderView.ResizeMode.Interactive)
        r_header.setSectionResizeMode(RangeCol.START_TIME, QHeaderView.ResizeMode.Interactive)
        r_header.setSectionResizeMode(RangeCol.END_TIME, QHeaderView.ResizeMode.Interactive)
        r_header.setSectionResizeMode(RangeCol.LABEL, QHeaderView.ResizeMode.Stretch)
        r_header.resizeSection(RangeCol.START_FRAME, 72)
        r_header.resizeSection(RangeCol.END_FRAME, 72)
        r_header.resizeSection(RangeCol.START_TIME, 88)
        r_header.resizeSection(RangeCol.END_TIME, 88)
        r_header.setMinimumSectionSize(67)

        v_header = self.ranges_view.verticalHeader()
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        v_header.setDefaultSectionSize(24)

        range_layout.addWidget(self.ranges_view)
        self.splitter.addWidget(self.range_container)

    def setup_shortcuts(self) -> None:
        self.api.register_shortcut(
            ShortcutDefinition.REMOVE_SCENE.definition,
            self.on_remove_scene_triggered,
            self.scenes_view,
            context=Qt.ShortcutContext.WidgetShortcut,
        )
        self.api.register_action(
            ShortcutDefinition.TOGGLE_RANGE_START.definition,
            self.range_start_action,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_action(
            ShortcutDefinition.TOGGLE_RANGE_END.definition,
            self.range_end_action,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_action(
            ShortcutDefinition.VALIDATE_RANGE.definition,
            self.add_range_action,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_action(
            ShortcutDefinition.ADD_SINGLE_FRAME.definition,
            self.add_frame_action,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_action(
            ShortcutDefinition.REMOVE_RANGE.definition,
            self.remove_range_action,
            context=Qt.ShortcutContext.WidgetShortcut,
        )

        self.api.register_shortcut(
            ShortcutDefinition.SEEK_PREV_BOUND.definition,
            self.on_seek_prev_bound,
            self.ranges_view,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_shortcut(
            ShortcutDefinition.SEEK_NEXT_BOUND.definition,
            self.on_seek_next_bound,
            self.ranges_view,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_shortcut(
            ShortcutDefinition.SEEK_PREV_RANGE.definition,
            self.on_seek_prev_range,
            self.ranges_view,
            context=Qt.ShortcutContext.WindowShortcut,
        )
        self.api.register_shortcut(
            ShortcutDefinition.SEEK_NEXT_RANGE.definition,
            self.on_seek_next_range,
            self.ranges_view,
            context=Qt.ShortcutContext.WindowShortcut,
        )

        self.api.register_shortcut(
            ShortcutDefinition.CYCLE_TOOLBAR_STYLE.definition,
            self.on_cycle_toolbar_style,
            self,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
        )

    def load_settings(self) -> None:
        self._color_gen = ColorGenerator(None)

        # Set the next color based on the last scene
        if scenes := self.settings.local_.scenes:
            next(self._color_gen)
            self._color_gen.send(scenes[-1].color)

        self._counter = count(len(scenes) + 1)

        # Load scenes from settings
        for scene in self.settings.local_.scenes:
            self.scenes_model.add_scene(scene, emit_signal=False)

    @override
    def on_workspace_loaded(self) -> None:
        self.output_map.clear()
        self.output_map.update((out.vs_index, out.vs_name) for out in self.api.voutputs)

    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        cachedproperty.clear_cache(self.ranges_model)

        self.on_scene_selection_changed()

        return super().on_current_voutput_changed(voutput, tab_index)

    def on_reload_icon(self) -> None:
        cachedproperty.clear_cache(self.scenes_delegate)
        self.scenes_view.viewport().update()
        self._update_action_labels()

    def on_new_scene(self) -> None:
        new_scene = SceneRow(color=next(self._color_gen), name=f"New Scene {str(next(self._counter)).zfill(2)}")

        idx = self.scenes_model.add_scene(new_scene)
        self.scenes_view.scrollTo(idx)
        self.scenes_view.setCurrentIndex(idx)

    def on_import_scene(self) -> None:
        load_plugins()

        parsers: list[Parser] = internal_parsers + list(flatten(manager.hook.vsview_scening_register_parser()))

        filters = {f"{p.filter.label} (*.{' *.'.join(to_arr(p.filter.suffix))})": p for p in parsers}
        files, selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Import scene file(s)",
            selectedFilter=self.settings.global_.last_selected_parser,
            filter=";;".join(sorted(filters)),
        )
        self.settings.global_.last_selected_parser = selected_filter

        if not files:
            logger.info("No file selected")
            return

        def on_success(result: list[SceneRow]) -> None:
            self.scenes_model.add_scene(result)
            self.api.statusMessage.emit(f"Importing {', '.join(files)} completed")

        self.parse_imported_files(files, filters[selected_filter]).map(on_success, on_loop=True)

    @run_in_background(name="ParseImportedFiles")
    def parse_imported_files(self, files: list[str], parser: Parser) -> list[SceneRow]:
        src_fps = self.api.current_voutput.vs_output.clip.fps

        scenes = list[SceneRow]()

        with monkey_patch_parser(parser, self._color_gen):
            for file in files:
                pfile = Path(file)
                try:
                    with pfile.open("rb") as f:
                        parsed = parser.parse(f, pfile.stem, src_fps)
                        scenes.extend([parsed] if isinstance(parsed, SceneRow) else parsed)
                except Exception:
                    logger.exception("Error parsing file: %s", file)

        return scenes

    def on_export_scene(self) -> None:
        load_plugins()

        serializers: list[Serializer] = internal_serializers + list(
            flatten(manager.hook.vsview_scening_register_serializer())
        )

        filters = {f"{p.filter.label} (*.{' *.'.join(to_arr(p.filter.suffix))})": p for p in serializers}
        file, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export scene file",
            filter=";;".join(sorted(filters)),
        )

        if not file:
            logger.info("Export aborted")
            return

        f = self.serialize_exported_scene(file, filters[selected_filter])
        f.map(lambda _: self.api.statusMessage.emit(f"Exporting {file} completed"), on_loop=True)

    @run_in_background(name="SerializeExportedCcene")
    def serialize_exported_scene(self, file: str, serializer: Serializer) -> None:
        scene: SceneRow = self.scenes_view.selectionModel().selectedRows()[0].data(self.scenes_model.SceneRowRole)

        v = self.api.current_voutput

        try:
            with Path(file).open("wb") as f:
                serializer.serialize(f, (UnifiedRange(r, v.frame_to_time, v.time_to_frame) for r in scene.ranges))
        except Exception:
            logger.exception("Error exporting file: %s", file)

    def on_remove_scene_triggered(self) -> None:
        if not (selected_indexes := self.scenes_view.selectionModel().selectedRows()):
            return

        rows = sorted((idx.row() for idx in selected_indexes), reverse=True)

        self.scenes_model.remove_scene(rows)

    def on_scene_selection_changed(self) -> None:
        if selected_indexes := self.scenes_view.selectionModel().selectedRows():
            self.range_container.setEnabled(True)
            if len(selected_indexes) == 1:
                self.export_scene_action.setEnabled(True)
            else:
                self.export_scene_action.setEnabled(False)

            all_scenes = list[SceneRow]()

            for index in selected_indexes:
                all_scenes.append(index.data(self.scenes_model.SceneRowRole))

            self.ranges_model.set_scenes(all_scenes)

            # Add displayable scenes to the timeline
            for scene in all_scenes:
                self._refresh_scene_on_timeline(scene, update=False)

            # Remove old references
            self.api.timeline.clear_notches(
                (scene.notch_id for scene in set(self.settings.local_.scenes).difference(all_scenes)),
                update=True,
            )
        else:
            self.range_container.setDisabled(True)
            self.export_scene_action.setDisabled(True)
            self.range_start_action.setChecked(False)

            self.api.timeline.clear_notches(scene.notch_id for scene in self.settings.local_.scenes)

    def on_cycle_toolbar_style(self) -> None:
        if (current_style := self.scenes_toolbar.toolButtonStyle()) != self.range_toolbar.toolButtonStyle():
            logger.warning("Toolbar styles mismatch. Forcing update.")

        styles = islice(GlobalSettings.toolbar_styles, current_style.value + 1, None)
        self.settings.global_.toolbar_style = next(styles)
        self._update_toolbar_style()

    def on_range_selection_changed(self) -> None:
        selected = self.ranges_view.selectionModel().selectedRows()

        if len(selected) == 1:
            start, _ = self.ranges_model.data(selected[0], self.ranges_model.RangeRole).as_frames(
                self.api.current_voutput
            )
            self.api.playback.seek(start)

    def on_range_start_toggle(self, checked: bool) -> None:
        if checked:
            self._pending_start = self.api.current_frame if self.api.timeline.mode == "frame" else self.api.current_time

            if self.range_end_action.isChecked():
                self.add_range_action.setEnabled(True)
        else:
            self._pending_start = None
            self.add_range_action.setDisabled(True)

    def on_range_end_toggle(self, checked: bool) -> None:
        if checked:
            self._pending_end = self.api.current_frame if self.api.timeline.mode == "frame" else self.api.current_time

            if self.range_start_action.isChecked():
                self.add_range_action.setEnabled(True)
        else:
            self._pending_end = None
            self.add_range_action.setDisabled(True)

    def on_add_range_triggered(self) -> None:
        selected_indexes = self.scenes_view.selectionModel().selectedRows()

        # Shouldn't happen
        if not selected_indexes or self._pending_start is None or self._pending_end is None:
            raise NotImplementedError

        index = next(reversed(selected_indexes))
        scene: SceneRow = index.data(self.scenes_model.SceneRowRole)
        self._add_range(self._pending_start, self._pending_end, scene)

    def on_add_frame_triggered(self) -> None:
        selected_indexes = self.scenes_view.selectionModel().selectedRows()

        # Shouldn't happen
        if not selected_indexes:
            raise NotImplementedError

        index = next(reversed(selected_indexes))
        scene: SceneRow = index.data(self.scenes_model.SceneRowRole)
        current = self.api.current_frame if self.api.timeline.mode == "frame" else self.api.current_time
        self._add_range(current, current, scene)

    def on_remove_range_triggered(self) -> None:
        if not (selected_indexes := self.ranges_view.selectionModel().selectedRows()):
            return

        # Sort by row descending to avoid index shifts
        for idx in sorted(selected_indexes, key=lambda idx: idx.row(), reverse=True):
            r = self.ranges_model.data(idx, self.ranges_model.RangeRole)
            scene = self.ranges_model.data(idx, self.ranges_model.SceneRowRole)

            self.ranges_model.remove_range(idx)
            self.api.timeline.discard_notch(scene.notch_id, [r.to_tuple()], r.id, update=False)

        self.api.timeline.update()

    def on_seek_prev_bound(self) -> None:
        self._seek_to_neighbor(self._get_visible_range_boundaries(), forward=False)

    def on_seek_next_bound(self) -> None:
        self._seek_to_neighbor(self._get_visible_range_boundaries(), forward=True)

    def on_seek_prev_range(self) -> None:
        self._seek_to_neighbor(self._get_visible_range_boundaries(starts_only=True), forward=False)

    def on_seek_next_range(self) -> None:
        self._seek_to_neighbor(self._get_visible_range_boundaries(starts_only=True), forward=True)

    def _add_range(self, start: Frame | Time, end: Frame | Time, scene: SceneRow) -> None:
        r: RangeFrame | RangeTime

        if isinstance(start, Frame):
            if not isinstance(end, Frame):
                end = self.api.current_voutput.time_to_frame(end)
            s, e = sorted([start, end])
            r = RangeFrame(start=s, end=e)
        elif isinstance(start, Time):
            if not isinstance(end, Time):
                end = self.api.current_voutput.frame_to_time(end)
            s, e = sorted([start, end])
            r = RangeTime(start=s, end=e)

        self.ranges_model.add_range(r, scene)

        self.ranges_view.scrollToBottom()
        self.range_start_action.setChecked(False)
        self.range_end_action.setChecked(False)

        self.api.timeline.add_notch(scene.notch_id, [r.to_tuple()], scene.color, r.label, r.id)

    def _persist_scenes(self) -> None:
        scenes = self.scenes_model.scenes.copy()
        self.settings.local_.scenes = scenes

        if scenes:
            self._color_gen.send(scenes[-1].color)

    def _refresh_scene_on_timeline(self, scene: SceneRow, *, update: bool = True) -> None:
        self.api.timeline.clear_notches(scene.notch_id, update=False)

        if scene.display and (not scene.checked_outputs or self.api.current_voutput.vs_index in scene.checked_outputs):
            for r in scene.ranges:
                self.api.timeline.add_notch(scene.notch_id, [r.to_tuple()], scene.color, r.label, r.id, update=False)

        if update:
            self.api.timeline.update()

    def _refresh_range_on_timeline(self, r: RangeFrame | RangeTime, scene: SceneRow, *, update: bool = True) -> None:
        self.api.timeline.discard_notch(scene.notch_id, [r.to_tuple()], r.id, update=False)
        self.api.timeline.add_notch(scene.notch_id, [r.to_tuple()], scene.color, r.label, r.id, update=False)

        if update:
            self.api.timeline.update()

    def _selected_ranges(self) -> list[RangeFrame | RangeTime]:
        return [idx.data(self.ranges_model.RangeRole) for idx in self.ranges_view.selectionModel().selectedRows()]

    def _copy_ranges_frames(self) -> None:
        v = self.api.current_voutput
        text = ", ".join(str(r.as_frames(v)) for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _copy_ranges_start_frames(self) -> None:
        v = self.api.current_voutput
        text = ", ".join(str(r.as_frames(v)[0]) for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _copy_ranges_end_frames(self) -> None:
        v = self.api.current_voutput
        text = ", ".join(str(r.as_frames(v)[1]) for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _copy_ranges_timestamps(self) -> None:
        v = self.api.current_voutput
        parts = list[str]()

        for r in self._selected_ranges():
            s, e = r.as_times(v)
            parts.append(f'("{s.to_ts()}", "{e.to_ts()}")')

        QApplication.clipboard().setText(f"[{', '.join(parts)}]")

    def _copy_ranges_start_timestamps(self) -> None:
        v = self.api.current_voutput
        text = ", ".join(f'"{r.as_times(v)[0].to_ts()}"' for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _copy_ranges_end_timestamps(self) -> None:
        v = self.api.current_voutput
        text = ", ".join(f'"{r.as_times(v)[1].to_ts()}"' for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _copy_ranges_labels(self) -> None:
        text = ", ".join(f'"{r.label}"' for r in self._selected_ranges())

        QApplication.clipboard().setText(f"[{text}]")

    def _on_ranges_context_menu(self, pos: QPoint) -> None:
        if not self.ranges_view.selectionModel().selectedRows():
            return

        menu = QMenu(self.ranges_view)
        menu.addAction(self.copy_frames_action)
        menu.addAction("Copy start frames only", self._copy_ranges_start_frames)
        menu.addAction("Copy end frames only", self._copy_ranges_end_frames)
        menu.addSeparator()

        menu.addAction("Copy timestamps", self._copy_ranges_timestamps)
        menu.addAction("Copy start timestamps only", self._copy_ranges_start_timestamps)
        menu.addAction("Copy end timestamps only", self._copy_ranges_end_timestamps)
        menu.addSeparator()

        menu.addAction("Copy labels", self._copy_ranges_labels)
        menu.addSeparator()

        menu.addAction(self.remove_range_action)

        menu.exec(self.ranges_view.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _get_visible_range_boundaries(self, starts_only: bool = False) -> list[int]:
        v = self.api.current_voutput
        points = set[int]()

        for r, _ in self.ranges_model.ranges:
            start, end = r.as_frames(v)
            points.add(start)

            if not starts_only:
                points.add(end)

        return sorted(points)

    def _seek_to_neighbor(self, points: list[int], forward: bool) -> None:
        if not points:
            return

        current = int(self.api.current_frame)

        if forward:
            idx = bisect_right(points, current)

            if idx < len(points):
                self.api.playback.seek(points[idx])
        else:
            idx = bisect_left(points, current)

            if idx > 0:
                self.api.playback.seek(points[idx - 1])

    def _update_ranges_header_width(self) -> None:
        rows = self.ranges_model.rowCount()
        width = self.ranges_view.verticalHeader().fontMetrics().horizontalAdvance(str(rows)) + 12
        self.ranges_view.verticalHeader().setFixedWidth(max(width, 24))

    def _update_action_labels(self) -> None:
        def set_text(action: QAction, action_id: str, base_text: str) -> None:
            key = self.api.get_shortcut_label(action_id)
            action.setIconText(f"{base_text} ({key})" if key else base_text)

        set_text(self.range_start_action, ShortcutDefinition.TOGGLE_RANGE_START.definition, "Mark in")
        set_text(self.range_end_action, ShortcutDefinition.TOGGLE_RANGE_END.definition, "Mark out")
        set_text(self.add_range_action, ShortcutDefinition.VALIDATE_RANGE.definition, "Add range")
        set_text(self.add_frame_action, ShortcutDefinition.ADD_SINGLE_FRAME.definition, "Add frame")

    def _update_toolbar_style(self) -> None:
        style = self.settings.global_.toolbar_style
        self.scenes_toolbar.setToolButtonStyle(style)
        self.range_toolbar.setToolButtonStyle(style)
