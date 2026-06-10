import ast
import asyncio
from collections.abc import Sequence
from concurrent.futures import CancelledError, Future
from functools import cache
from logging import getLogger
from pathlib import Path
from typing import Annotated, Any, override
from uuid import uuid4

from pydantic import BaseModel, Field
from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTime, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from vsview.api import (
    Accordion,
    ActionDefinition,
    Frame,
    FrameEdit,
    IconName,
    IconReloadMixin,
    Login,
    PluginAPI,
    SegmentedControl,
    Time,
    TimeEdit,
    VideoOutputProxy,
    WidgetPluginBase,
    run_in_background,
    run_in_loop,
)

from ._metadata import LOGIN_CONTEXT, PLUGIN_DISPLAY, PLUGIN_ID
from .models import ComparisonImage, ComparisonSource, SlowPicsSources, TMDBTitle
from .ui import (
    BrowserID,
    FrameSourceProvider,
    FrameThumbnailList,
    LineEditCompleter,
    MainCompWidget,
    OutputDropdown,
    ProgressBar,
    TagsLineEdit,
    TagsListPopup,
    ThumbnailItem,
    TMDBListPopup,
)
from .worker import ExtractFramesWorker, SelectFrameWorker, SlowPicsWorker, Tag, TMDBWorker

logger = getLogger(__name__)


class GlobalSettings(BaseModel):
    browser_id: Annotated[
        str,
        BrowserID(
            label="Browser ID",
            tooltip=(
                "This browser ID identifies your uploads on slow.pics. "
                "Copy it to your browser cookies to claim comparisons."
            ),
        ),
    ] = Field(default_factory=lambda: str(uuid4()))

    tmdb_format: Annotated[
        str,
        LineEditCompleter(
            label="TMDB Format name",
            tooltip="The format used for the 'Collection' name when a TMDB show is selected.\n"
            'This is only applied if the "Collection" field is currently empty.\n\n'
            "Available fields:\n"
            + "\n".join(f'- "{{{fmt}}}": {doc}' for fmt, doc in TMDBTitle.format_hints.items())
            + '\n- "{vs_names}": Appended outputs names (e.g. "Source VS Encode")',
        ),
    ] = "{name} ({year}) - {vs_names}"

    login: Annotated[
        str,
        Login(
            label="Slow.pics credentials",
            namespace=PLUGIN_ID,
            context=LOGIN_CONTEXT,
            tooltip="The Slowpoke.pics credentials for login",
        ),
    ] = ""

    pict_types_i: bool = True
    pict_types_p: bool = True
    pict_types_b: bool = True
    combed: bool = False
    public_comp_default: bool = True


class CompPlugin(WidgetPluginBase[GlobalSettings, None], IconReloadMixin):
    identifier = PLUGIN_ID
    display_name = PLUGIN_DISPLAY

    shortcuts = (ActionDefinition(f"{PLUGIN_ID}.add_current_frame", "Add Current Frame", "Ctrl+Space"),)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        self._pending_select_frames: Future[Any] | None = None
        self._pending_extract_frames: Future[list[tuple[int, Path]]] | None = None
        self._pending_upload: Future[str] | Future[None] | None = None
        self._pending_tags: Future[list[Tag]] | None = None
        self._select_frames_worker: SelectFrameWorker | None = None
        self._extract_frames_worker: ExtractFramesWorker | None = None
        self._is_upload_cancelled = False
        self._extraction_finished = False
        self._extract_paths: list[tuple[int, Path]] | None = None
        self._reported_url = ""

        # Build UI
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        with MainCompWidget(self) as main:
            self._setup_clip_options(main)
            self._setup_upload_settings(main)
            self._setup_actions(main)

        self._current_outputs = self.outputs_dropdown.included_outputs
        self._update_buttons_state()

        main_layout.addWidget(main, 1)

        self.progress_stack = QStackedWidget(self)
        self.progress_stack.hide()

        self.progress_container = QWidget(self.progress_stack)
        progress_layout = QHBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)

        self.progress_bar = ProgressBar(self.progress_container)
        self.progress_bar.progressRunning.connect(self.set_progress_bar_on_top)

        self.cancel_btn = self.make_tool_button(IconName.X_CIRCLE, "Cancel operation", self.progress_container)
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)

        progress_layout.addWidget(self.progress_bar, 1)
        progress_layout.addWidget(self.cancel_btn)

        self.reported_url_container = QWidget(self.progress_stack)
        reported_url_layout = QHBoxLayout(self.reported_url_container)
        reported_url_layout.setContentsMargins(0, 0, 0, 0)
        reported_url_layout.setSpacing(0)
        self.url_label = QLabel(
            self.reported_url_container,
            textFormat=Qt.TextFormat.RichText,
            openExternalLinks=True,
            textInteractionFlags=Qt.TextInteractionFlag.TextBrowserInteraction,
        )
        self.url_copy_btn = self.make_tool_button(IconName.CLIPBOARD, "Copy Slow.pics URL", self.reported_url_container)
        self.url_copy_btn.clicked.connect(self.on_url_copy_btn_clicked)
        reported_url_layout.addStretch()
        reported_url_layout.addWidget(self.url_label)
        reported_url_layout.addWidget(self.url_copy_btn)

        self.progress_stack.addWidget(self.progress_container)
        self.progress_stack.addWidget(self.reported_url_container)
        self.progress_stack.setCurrentWidget(self.progress_container)

        main_layout.addWidget(self.progress_stack)

        self.slowpics_worker = SlowPicsWorker(self.api, self.settings, self.secrets, self.progress_bar)

        self.api.register_action(
            f"{PLUGIN_ID}.add_current_frame",
            self.add_frame_act,
            context=Qt.ShortcutContext.WindowShortcut,
        )

        self.api.register_on_destroy(self.init_load.cache_clear)

        # Disable the whole plugin if we don't have a local storage
        self.setEnabled(has_file := bool(self.api.file_path))
        if not has_file:
            self.setToolTip("Plugin disabled: the current workspace doesn't have a local storage.")

        # Install event filter to override child tooltips when disabled
        if not (app := QApplication.instance()):
            raise SystemError

        app.installEventFilter(self)
        self.destroyed.connect(lambda: app.removeEventFilter(self) if (app := QApplication.instance()) else None)

    def _setup_clip_options(self, main: MainCompWidget) -> None:
        self.clip_section = Accordion("Clip Options", main)
        self.clip_section.setToolTip("Configure source clips and select frames for comparison")

        form = self.clip_section.add_form_layout()

        self.outputs_dropdown = OutputDropdown(self.clip_section)
        self.outputs_dropdown.setToolTip("Select and rename outputs.")
        self.outputs_dropdown.inclusionChanged.connect(self._update_buttons_state)
        form.addRow(self.outputs_dropdown)

        # Current frames + add remove buttons
        frame_widget = QWidget(self.clip_section)
        frame_row = QVBoxLayout(frame_widget)
        frame_row.setContentsMargins(0, 0, 0, 0)
        frame_row.setSpacing(4)

        toolbar = QToolBar(
            frame_widget,
            movable=False,
            toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )

        self.add_frame_act = self.make_action(IconName.PLUS, "Add current frame to the list", frame_widget)
        self.add_frame_act.setIconText("Add current frame")

        menu = QMenu(frame_widget)
        self.add_multi_frames_act = menu.addAction("Add multiple frames...")
        self.add_multi_frames_act.triggered.connect(self.on_add_multiple_frames)
        self.add_frame_act.setMenu(menu)

        toolbar.addAction(self.add_frame_act)
        if isinstance(add_btn := toolbar.widgetForAction(self.add_frame_act), QToolButton):
            add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            add_btn.setStyleSheet("QToolButton::menu-button:hover {background-color: rgba(255, 255, 255, 0.1)}")

        self.remove_frame_act = self.make_action(IconName.MINUS, "Remove selected frame(s) from the list", frame_widget)
        self.remove_frame_act.setIconText("Remove selected frame(s)")
        self.remove_frame_act.setEnabled(False)

        toolbar.addAction(self.remove_frame_act)

        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.frames_count_label = QLabel("0 frames", frame_widget)
        self.frames_count_label.setStyleSheet("padding-right: 6px; color: palette(placeholder-text); font-weight: 500;")
        toolbar.addWidget(self.frames_count_label)

        self.frames_list = FrameThumbnailList(self.api, frame_widget)
        self.frames_list.setToolTip("Double-click to seek to frame. Use 'Delete' to remove.")
        self.frames_list.itemDoubleClicked.connect(
            lambda item: self.api.playback.seek(
                self.api.current_voutput.time_to_frame(item.data(Qt.ItemDataRole.UserRole))
            )
        )
        self.frames_list.itemSelectionChanged.connect(
            lambda: self.remove_frame_act.setEnabled(len(self.frames_list.selectedItems()) > 0)
        )
        self.frames_list.listSizeChanged.connect(self.on_list_size_changed)

        self.add_frame_act.triggered.connect(lambda: self.frames_list.add_item(get_pict_type=self.pict_types_supported))
        self.remove_frame_act.triggered.connect(self.frames_list.remove_selected)

        frame_row.addWidget(toolbar)
        frame_row.addWidget(self.frames_list)
        form.addRow(frame_widget)

        # Group box for the Auto Select automation
        auto_select_container = QGroupBox("Auto Select", self.clip_section)
        auto_select_container.setToolTip("Automated frame selection tools based on brightness and picture type")
        auto_select_frame_layout = QFormLayout(auto_select_container)

        # Frame counts
        frame_count_widget = QWidget(auto_select_container)
        frame_count_layout = QHBoxLayout(frame_count_widget)
        frame_count_layout.setContentsMargins(0, 0, 0, 0)
        frame_count_layout.setSpacing(8)

        self.random_frame_count = FrameEdit(frame_count_widget)
        self.random_frame_count.setMaximum(9999)
        self.random_frame_count.setValue(0)
        self.random_frame_count.setFixedWidth(80)
        self.random_frame_count.setToolTip("Total number of random frames to select")
        self.random_frame_count.frameChanged.connect(self.on_random_frame_count_changed)

        self.dark_frame_count = FrameEdit(frame_count_widget)
        self.dark_frame_count.setMaximum(9999)
        self.dark_frame_count.setValue(0)
        self.dark_frame_count.setFixedWidth(80)
        self.dark_frame_count.setToolTip("Number of random darkest frames to include")

        self.light_frame_count = FrameEdit(frame_count_widget)
        self.light_frame_count.setMaximum(9999)
        self.light_frame_count.setValue(0)
        self.light_frame_count.setFixedWidth(80)
        self.light_frame_count.setToolTip("Number of random lightest frames to include")

        frame_count_layout.addWidget(self.random_frame_count)
        frame_count_layout.addStretch()
        frame_count_layout.addWidget(QLabel("Dark:"))
        frame_count_layout.addWidget(self.dark_frame_count)
        frame_count_layout.addSpacing(4)
        frame_count_layout.addWidget(QLabel("Light:"))
        frame_count_layout.addWidget(self.light_frame_count)

        auto_select_frame_layout.addRow("Total Random:", frame_count_widget)

        # Range limit
        self.range_mode_selector = SegmentedControl(["Frame", "Time"], auto_select_container)
        self.range_mode_selector.setToolTip("Switch between frame-based and time-based range selection")
        auto_select_frame_layout.addRow("Range Unit:", self.range_mode_selector)

        self.range_stack = QStackedWidget(auto_select_container)

        # Frame/time range inputs
        frame_page = QWidget(self.range_stack)
        frame_row = QHBoxLayout(frame_page)
        frame_row.setContentsMargins(0, 0, 0, 0)
        frame_row.setSpacing(8)

        self.frame_edit_start = FrameEdit(frame_page)
        self.frame_edit_start.setToolTip("Start frame for comparison range")
        self.frame_edit_start.frameChanged.connect(self.on_frame_edit_start_changed)

        self.frame_edit_end = FrameEdit(frame_page)
        self.frame_edit_end.setToolTip("End frame for comparison range")
        self.frame_edit_end.frameChanged.connect(self.on_frame_edit_end_changed)

        frame_row.addWidget(self.frame_edit_start)
        frame_row.addWidget(self.frame_edit_end)
        self.range_stack.addWidget(frame_page)

        time_page = QWidget(self.range_stack)
        time_row = QHBoxLayout(time_page)
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(8)

        self.time_edit_start = TimeEdit(time_page)
        self.time_edit_start.setToolTip("Start time for comparison range")
        self.time_edit_start.valueChanged.connect(self.on_time_edit_start_changed)

        self.time_edit_end = TimeEdit(time_page)
        self.time_edit_end.setToolTip("End time for comparison range")
        self.time_edit_end.valueChanged.connect(self.on_time_edit_end_changed)

        time_row.addWidget(self.time_edit_start)
        time_row.addWidget(self.time_edit_end)

        self.range_stack.addWidget(time_page)
        self.range_mode_selector.segmentChanged.connect(self.range_stack.setCurrentIndex)

        auto_select_frame_layout.addRow("Range:", self.range_stack)

        # Picture Type Group
        self.frame_props_container = QGroupBox("Frame Properties", auto_select_container)
        frame_props_layout = QHBoxLayout(self.frame_props_container)

        self.pict_type_i_cb = QCheckBox("I-Frame", self.frame_props_container)
        self.pict_type_i_cb.setToolTip("Include I-frames (Intra-coded)")
        self.pict_type_p_cb = QCheckBox("P-Frame", self.frame_props_container)
        self.pict_type_p_cb.setToolTip("Include P-frames (Predictive)")
        self.pict_type_b_cb = QCheckBox("B-Frame", self.frame_props_container)
        self.pict_type_b_cb.setToolTip("Include B-frames (Bi-predictive)")
        self.combed_cb = QCheckBox("Combed", self.frame_props_container)
        self.combed_cb.setToolTip("Include Combed frames")
        self.pict_type_i_cb.setChecked(self.settings.global_.pict_types_i)
        self.pict_type_p_cb.setChecked(self.settings.global_.pict_types_p)
        self.pict_type_b_cb.setChecked(self.settings.global_.pict_types_b)
        self.combed_cb.setChecked(self.settings.global_.combed)
        self.pict_type_i_cb.toggled.connect(lambda state: setattr(self.settings.global_, "pict_types_i", state))
        self.pict_type_p_cb.toggled.connect(lambda state: setattr(self.settings.global_, "pict_types_p", state))
        self.pict_type_b_cb.toggled.connect(lambda state: setattr(self.settings.global_, "pict_types_b", state))
        self.combed_cb.toggled.connect(lambda state: setattr(self.settings.global_, "combed", state))
        frame_props_layout.addWidget(self.pict_type_i_cb)
        frame_props_layout.addWidget(self.pict_type_p_cb)
        frame_props_layout.addWidget(self.pict_type_b_cb)
        frame_props_layout.addWidget(self.combed_cb)

        auto_select_frame_layout.addRow(self.frame_props_container)

        # The actual button
        self.select_frames_btn = QPushButton("Select frames", auto_select_container)
        self.select_frames_btn.setDisabled(True)
        self.select_frames_btn.setToolTip("Collect frames based on the current settings")
        self.select_frames_btn.clicked.connect(self.on_select_frames_clicked)

        auto_select_frame_layout.addRow(self.select_frames_btn)
        form.addRow(auto_select_container)

        self.extract_btn = QPushButton("Extract", self.clip_section)
        self.extract_btn.setDisabled(True)
        self.extract_btn.setToolTip("Extract selected frames to disk")
        self.extract_btn.clicked.connect(self.on_extract_btn_clicked)
        form.addRow(self.extract_btn)

        main.add_section(self.clip_section)

    def _setup_upload_settings(self, main: MainCompWidget) -> None:
        section = Accordion("Upload Settings", main)
        section.setToolTip("Configure upload settings for Slow.pics")
        form = section.add_form_layout()

        # Collection name
        self.collection_name = QLineEdit(section, placeholderText="Name for the image collection")
        self.collection_name.setToolTip("The name of the comparison collection")
        form.addRow("Collection:", self.collection_name)

        # Network stuff
        self.tmdb_title: TMDBTitle | None = None
        self.tmdb_worker = TMDBWorker()

        self.tmdb_name = QLineEdit(section, placeholderText="Search for a movie or TV show...")
        self.tmdb_name.setToolTip("Movie or TV show name to fetch metadata from TMDB")
        self.tmdb_name.textChanged.connect(self.on_tmdb_text_changed)
        self.tmdb_name.editingFinished.connect(self.on_tmdb_editing_finished)
        form.addRow("TMDB Name:", self.tmdb_name)

        self.tmdb_popup = TMDBListPopup(section, self.tmdb_name)
        self.tmdb_popup.itemClicked.connect(self.on_tmdb_item_selected)
        self.tmdb_popup.itemActivated.connect(self.on_tmdb_item_selected)

        self.tmdb_debounce_timer = QTimer(self, singleShot=True, interval=300)
        self.tmdb_debounce_timer.timeout.connect(self.perform_tmdb_search)

        self.tags = TagsLineEdit(section, placeholder_text="Search for additional informations...")
        self.tags.editingStarted.connect(self.on_tags_editing_started)
        self.tags.inputTextChanged.connect(self.on_tags_input_changed)
        self.tags.tagsChanged.connect(self.on_tags_input_changed)
        self.tags.setToolTip("Additional tags to help find the comparison")
        form.addRow("Tags:", self.tags)

        self.tags_popup = TagsListPopup(section, self.tags)
        self.tags_popup.tagPicked.connect(self.on_tag_item_selected)

        # Public / NSFW checkboxes
        flags_widget = QWidget(section)
        flags_row = QHBoxLayout(flags_widget)
        flags_row.setContentsMargins(0, 0, 0, 0)
        flags_row.setSpacing(16)

        self.public_check = QCheckBox("Public", flags_widget)
        self.public_check.setChecked(self.settings.global_.public_comp_default)
        self.public_check.setToolTip("Make the comparison publicly visible")
        self.public_check.toggled.connect(lambda state: setattr(self.settings.global_, "public_comp_default", state))
        self.nsfw_check = QCheckBox("NSFW", flags_widget)
        self.nsfw_check.setToolTip("Mark the comparison as Not Safe For Work")

        flags_row.addWidget(self.public_check)
        flags_row.addWidget(self.nsfw_check)
        flags_row.addStretch(1)

        form.addRow("Flags:", flags_widget)

        # Remove after N days
        self.remove_after = QSpinBox(section)
        self.remove_after.setRange(0, 999999)
        self.remove_after.setSpecialValueText("Never")
        self.remove_after.setSuffix(" days")
        self.remove_after.setToolTip("Remove comparison after N days (0 = never)")
        form.addRow("Auto-Remove:", self.remove_after)

        self.upload_btn = QPushButton("Upload", section)
        self.upload_btn.setDisabled(True)
        self.upload_btn.setToolTip("Upload extracted frames")
        self.upload_btn.clicked.connect(self.on_upload_btn_clicked)
        form.addRow(self.upload_btn)

        main.add_section(section)

    def _setup_actions(self, main: MainCompWidget) -> None:
        actions_widget = QWidget(main)
        actions_layout = QVBoxLayout(actions_widget)
        actions_layout.setContentsMargins(8, 4, 8, 4)
        actions_layout.setSpacing(6)

        self.do_all_btn = QPushButton("Extract && Upload", actions_widget)
        self.do_all_btn.setToolTip("Extract selected frames → Upload")
        self.do_all_btn.clicked.connect(self.on_do_all_btn_clicked)
        actions_layout.addWidget(self.do_all_btn)

        main.add_section(actions_widget)

    @property
    @run_in_loop(return_future=False)
    def pict_types_supported(self) -> bool:
        return self.pict_type_i_cb.isEnabled() and self.pict_type_p_cb.isEnabled() and self.pict_type_b_cb.isEnabled()

    @pict_types_supported.setter
    @run_in_loop(return_future=False)
    def pict_types_supported(self, value: bool) -> None:
        self.pict_type_i_cb.setEnabled(value)
        self.pict_type_p_cb.setEnabled(value)
        self.pict_type_b_cb.setEnabled(value)

    @property
    def comp_voutputs(self) -> list[VideoOutputProxy]:
        return [v for v in self.api.voutputs if v.kwargs.get("allow_comp", True)]

    @property
    def selected_voutputs(self) -> list[VideoOutputProxy]:
        included = self.outputs_dropdown.included_outputs
        return [v for v in self.comp_voutputs if v.vs_index in included]

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Intercept tooltips for this plugin tree when disabled
        if (
            event.type() == QEvent.Type.ToolTip
            and not self.isEnabled()
            and isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
        ):
            QToolTip.showText(QCursor.pos(), self.toolTip(), watched)
            return True
        return super().eventFilter(watched, event)

    @cache
    @run_in_loop(return_future=False)
    def init_load(self) -> None:
        voutputs = self.comp_voutputs

        max_total_frames = min(voutputs, key=lambda v: v.info.total_frames).info.total_frames
        shortest = min(voutputs, key=lambda v: v.info.total_duration)
        max_total_duration = shortest.frame_to_time(shortest.info.total_frames)

        self.outputs_dropdown.shortest_dur_text = f" - {max_total_duration.to_ts()} ({max_total_frames})"
        self.outputs_dropdown.populate(voutputs)
        self.frames_list.clear()
        self.frames_list.included_outputs = self.comp_voutputs
        self._update_frames_count()

        max_frame = max_total_frames - 1
        max_time = shortest.frame_to_time(shortest.info.total_frames - 1)

        self.frame_edit_start.setRange(0, max_frame)
        self.frame_edit_end.setRange(0, max_frame)

        self.frame_edit_start.setValue(0)
        self.frame_edit_end.setValue(max_frame)

        qtime_s = QTime()
        qtime_e = max_time.to_qtime()

        self.time_edit_start.setTime(qtime_s)
        self.time_edit_start.setMinimumTime(qtime_s)
        self.time_edit_start.setMaximumTime(qtime_e)

        self.time_edit_end.setTime(qtime_e)
        self.time_edit_end.setMinimumTime(qtime_s)
        self.time_edit_end.setMaximumTime(qtime_e)

        self._update_buttons_state()

    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        self.init_load()

        if self.pict_types_supported and not any("_PictType" in props for props in voutput.props.values()):
            self.pict_types_supported = False

        self.frames_list.update_thumbnails(voutput.vs_index)

    def set_progress_bar_on_top(self) -> None:
        self.progress_stack.setCurrentWidget(self.progress_container)
        self.progress_stack.show()

    def set_url_on_top(self) -> None:
        self.progress_stack.setCurrentWidget(self.reported_url_container)
        self.progress_stack.show()

    def on_frame_edit_start_changed(self, new: Frame, old: Frame) -> None:
        self.frame_edit_end.setMinimum(new)
        self.time_edit_start.setTime(self.api.current_voutput.frame_to_time(new).to_qtime())

    def on_frame_edit_end_changed(self, new: Frame, old: Frame) -> None:
        self.frame_edit_start.setMaximum(new)
        self.time_edit_end.setTime(self.api.current_voutput.frame_to_time(new).to_qtime())

    def on_time_edit_start_changed(self, new: QTime, old: QTime) -> None:
        self.time_edit_end.setMinimumTime(new)
        self.frame_edit_start.setValue(self.api.current_voutput.time_to_frame(Time.from_qtime(new)))

    def on_time_edit_end_changed(self, new: QTime, old: QTime) -> None:
        self.time_edit_start.setMaximumTime(new)
        self.frame_edit_end.setValue(self.api.current_voutput.time_to_frame(Time.from_qtime(new)))

    def on_list_size_changed(self, delta: int) -> None:
        self._extraction_finished = False
        self._update_buttons_state()
        self._update_frames_count()

    def _update_frames_count(self) -> None:
        count = self.frames_list.count()
        self.frames_count_label.setText(f"{count} {'frame' if count == 1 else 'frames'}")

        if count > 50:
            color = "red"
            self.frames_count_label.setToolTip("Too many frames selected! Uploading may not be supported.")
        else:
            color = "palette(placeholder-text)"
            self.frames_count_label.setToolTip("")

        self.frames_count_label.setStyleSheet(f"padding-right: 6px; color: {color}; font-weight: 500;")

    def on_random_frame_count_changed(self, new: Frame, old: Frame) -> None:
        self._update_buttons_state()

    def on_add_multiple_frames(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Add Multiple Frames",
            "Enter frames as a Python list or tuple (e.g. [1, 2, 3]):",
        )
        if not any([ok, text]):
            return
        try:
            frames = ast.literal_eval(text)
        except (SyntaxError, ValueError) as e:
            logger.error("Failed to parse frame list: %s", e)
            return

        if isinstance(frames, int):
            frames = [frames]
        elif not isinstance(frames, Sequence):
            logger.error("Invalid frame list format: %s", text)
            return

        for f in frames:
            if f in range(self.api.current_voutput.info.total_frames):
                self.frames_list.add_item(frame=f, get_pict_type=self.pict_types_supported)
            else:
                logger.warning("Skipping invalid frame number: %s", f)

    def on_select_frames_clicked(self) -> None:
        worker = SelectFrameWorker(self.api, self)

        if (worker.normal + worker.dark + worker.light) > 50:
            result = QMessageBox.warning(
                self,
                "Too Many Frames",
                "You are auto-selecting more than 50 frames.\n\n"
                "If you expect to upload the results to Slow.pics, this may not be supported."
                "\n\nPress OK to continue anyway, or Cancel to stop the selection.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                return

        @run_in_loop
        def on_finished(future: Future[list[tuple[Time, FrameSourceProvider]]]) -> None:
            self.progress_bar.reset_progress()
            self.progress_stack.hide()

            self._pending_select_frames = None
            self._select_frames_worker = None

            if exc := future.exception():
                if isinstance(exc, CancelledError):
                    logger.info("Frame selection cancelled.")
                else:
                    logger.error("Error during frame selection: %s", exc)
                self._update_buttons_state()
                return

            times = future.result()

            v = self.api.current_voutput
            for t, src_provider in times:
                self.frames_list.add_item(
                    frame=v.time_to_frame(t),
                    get_pict_type=self.pict_types_supported,
                    src_provider=src_provider,
                )

            self._update_buttons_state()

        self._select_frames_worker = worker
        self._pending_select_frames = worker.run()
        self._pending_select_frames.add_done_callback(on_finished)
        self._update_buttons_state()

    def on_extract_btn_clicked(self) -> None:
        @run_in_loop
        def prepare_and_extract(future: Future[Any] | None = None) -> None:
            if future and future.exception():
                return

            worker = ExtractFramesWorker(self.api, self)
            self._extract_frames_worker = worker
            self._pending_extract_frames = worker.run()

            @run_in_loop
            def on_finished(f: Future[list[tuple[int, Path]]]) -> None:
                self.clip_section.setEnabled(True)
                self.progress_bar.reset_progress()
                self.progress_stack.hide()
                self._pending_extract_frames = None
                self._extract_frames_worker = None
                if exc := f.exception():
                    if isinstance(exc, CancelledError):
                        logger.info("Frame extraction cancelled.")
                    else:
                        logger.error("Error during frame extraction: %s", exc)
                else:
                    self._extract_paths = f.result()
                    self._extraction_finished = True
                self._update_buttons_state()

            self._pending_extract_frames.add_done_callback(on_finished)
            self._update_buttons_state()

        self.clip_section.setDisabled(True)

        if self._pending_select_frames:
            self._pending_select_frames.add_done_callback(prepare_and_extract)
        else:
            prepare_and_extract()

    def on_tmdb_text_changed(self, text: str) -> None:
        self.tmdb_title = None
        if not text.strip():
            self.tmdb_debounce_timer.stop()
            self.tmdb_popup.hide()
            return

        self.tmdb_debounce_timer.start()

    def on_tmdb_editing_finished(self) -> None:
        text = self.tmdb_name.text().strip()

        if not text:
            self.tmdb_title = None
            self.tmdb_popup.hide()
            return

        if self.tmdb_title and self.tmdb_title.name == text:
            return

        with QSignalBlocker(self.tmdb_name):
            self.tmdb_name.clear()

        self.tmdb_title = None
        self.tmdb_popup.hide()

    def perform_tmdb_search(self) -> None:
        if not (query := self.tmdb_name.text().strip()):
            self.tmdb_popup.hide()
            return

        @run_in_loop
        def on_finished(future: Future[list[TMDBTitle]]) -> None:
            QApplication.restoreOverrideCursor()
            if future.exception():
                self.tmdb_popup.hide()
                return

            if self.tmdb_name.text().strip() != query:
                return

            results = future.result()

            if not results:
                self.tmdb_popup.show_no_results()
            else:
                self.tmdb_popup.show_results(results)

        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        future = self.tmdb_worker.search(query)
        future.add_done_callback(on_finished)

    def on_tmdb_item_selected(self, item: QListWidgetItem) -> None:
        title: TMDBTitle = item.data(Qt.ItemDataRole.UserRole)

        self.tmdb_popup.hide()

        with QSignalBlocker(self.tmdb_name):
            self.tmdb_name.setText(title.name)

        self.tmdb_title = title

        # Automatically set the collection name if it's currently empty
        if not self.collection_name.text().strip():
            voutputs = self.selected_voutputs
            vs_names = " VS ".join(v.vs_name for v in voutputs)
            self.collection_name.setText(title.format_name(self.settings.global_.tmdb_format, vs_names=vs_names))

    def on_tags_editing_started(self) -> None:
        if self.tags_popup.has_tags():
            self.on_tags_input_changed()
            return

        if self._pending_tags:
            return

        @run_in_loop
        def on_finished(future: Future[list[Tag]]) -> None:
            QApplication.restoreOverrideCursor()
            self._pending_tags = None
            if future.exception():
                self.tags_popup.hide()
                return

            self.tags_popup.set_tags(future.result())
            self.on_tags_input_changed()

        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        self._pending_tags = self.slowpics_worker.get_tags()
        self._pending_tags.add_done_callback(on_finished)

    def on_tags_input_changed(self, *_: Any) -> None:
        self.tags_popup.show_filtered(self.tags.input_text(), set(self.tags.selected_tags()))

    def on_tag_item_selected(self, value: str, label: str) -> None:
        self.tags.add_tag(value, label)
        self.tags.clear()
        self.on_tags_input_changed()

    def on_upload_btn_clicked(self) -> None:
        if not self._extract_paths:
            logger.error("No extracted frames to upload")
            return

        data = self.frames_list.get_data()

        self._pending_upload = self._prepare_and_upload(data)
        self._update_buttons_state()

    def on_do_all_btn_clicked(self) -> None:
        self.on_extract_btn_clicked()

        @run_in_loop
        def after_extract(future: Future[Any] | None = None) -> None:
            if future and future.exception():
                return
            if self._extraction_finished:
                self.on_upload_btn_clicked()

        if self._pending_extract_frames:
            self._pending_extract_frames.add_done_callback(after_extract)
        else:
            after_extract()

    @run_in_background(name="PrepareAndUpload")
    def _prepare_and_upload(
        self,
        data: list[tuple[Time, dict[int, ThumbnailItem.Metadata], FrameSourceProvider]],
    ) -> None:
        if not self._extract_paths:
            logger.error("No extracted frames to upload")
            self._on_upload_error()
            return

        src_providers = [src_provider for _, _, src_provider in data]
        autogenerated_count = len([p for p in src_providers if p.name.startswith("RANDOM")])

        if len(src_providers) > 100:
            logger.error("Too many images selected for upload: %d", len(src_providers))
            self._on_upload_error("Too many images selected. Slow.pics supports at most 100 images per upload.")
            return

        if autogenerated_count > 50:
            logger.error(
                "Too many autogenerated images selected for upload: %d",
                autogenerated_count,
            )
            self._on_upload_error(
                "Too many autogenerated images selected. Slow.pics supports at most 50 autogenerated images per upload."
            )
            return

        # Build sources from the extracted images on disk
        sources = list[ComparisonSource]()
        vouputs = {v.vs_index: v for v in self.selected_voutputs}

        for index, source_dir in self._extract_paths:
            images = list[ComparisonImage]()
            output = vouputs[index]

            for time, metadata, _ in data:
                frame = output.time_to_frame(time)
                image_path = next(source_dir.glob(f"*{frame}.png"), None)

                if not image_path:
                    logger.error("Missing extracted image for frame %s in %s", frame, source_dir)
                    self._on_upload_error()
                    return

                timestamp = time.to_ts("{M:02d}:{S:02d}.{ms:03d}")
                images.append(ComparisonImage(image_path, metadata[output.vs_index].pict_type, frame, timestamp))

            sources.append(ComparisonSource(output.vs_name, images))

        if not sources or not any(images for _, images in sources):
            logger.error("No images found to upload")
            self._on_upload_error()
            return

        # Run the upload
        self._is_upload_cancelled = False

        @run_in_loop
        def on_cookies(future: Future[dict[str, str]]) -> None:
            if self._is_upload_cancelled or future.exception():
                self._on_upload_error()
                return

            # Once we have the cookies, run the upload
            src = SlowPicsSources(
                collection_name=self.collection_name.text() or "Untitled",
                sources=sources,
                public=self.public_check.isChecked(),
                nsfw=self.nsfw_check.isChecked(),
                tmdb_id=f"{self.tmdb_title.media_tag}_{self.tmdb_title.id}" if self.tmdb_title else None,
                remove_after=self.remove_after.value(),
                tags=self.tags.selected_tags(),
            )
            self._pending_upload = self.slowpics_worker.upload(src=src, cookies=future.result())

            # Once the upload is finished, reset the progress bar and update the buttons state
            @run_in_loop
            def on_upload_finished(f: Future[str]) -> None:
                self.progress_bar.reset_progress()
                self.progress_stack.hide()
                self._pending_upload = None

                if exc := f.exception():
                    if isinstance(exc, asyncio.CancelledError):
                        logger.info("Upload cancelled.")
                    else:
                        logger.error("Error during upload: %s", exc)
                else:
                    url = f.result()
                    logger.info("Upload complete: %s", url)
                    self._reported_url = url
                    self.url_label.setText(f'<a href="{url}">{url}</a>')
                    self.set_url_on_top()

                self._update_buttons_state()
                self.upload_btn.setDisabled(True)

            self._pending_upload.add_done_callback(on_upload_finished)

        # Get the cookies and then upload
        cookies_future = self.slowpics_worker.get_cookies()
        cookies_future.add_done_callback(on_cookies)

    @run_in_loop(return_future=False)
    def _on_upload_error(self, message: str | None = None) -> None:
        # Helper to cleanup UI on failure from background
        self.progress_bar.reset_progress()
        self.progress_stack.hide()
        self.clip_section.setEnabled(True)
        self._pending_upload = None
        self._update_buttons_state()
        if message:
            QMessageBox.critical(self, "Upload Error", message)

    def _update_buttons_state(self) -> None:
        current_outputs = self.outputs_dropdown.included_outputs

        is_idle = not (self._pending_select_frames or self._pending_extract_frames or self._pending_upload)
        has_outputs = bool(current_outputs)
        has_frames = bool(self.frames_list.get_data())

        can_operate = has_outputs and is_idle
        frames_ready = can_operate and has_frames

        # Needs extraction if it's not finished, OR if the outputs have changed since last time
        needs_extraction = not self._extraction_finished or (current_outputs != self._current_outputs)

        self.select_frames_btn.setEnabled(can_operate and self.random_frame_count.value() > 0)

        # Extract and Do All share the exact same requirements
        can_extract = frames_ready and needs_extraction
        self.extract_btn.setEnabled(can_extract)
        self.do_all_btn.setEnabled(can_extract)

        # Upload is the exact logical opposite of needing an extraction
        self.upload_btn.setEnabled(frames_ready and not needs_extraction)

        self._current_outputs = current_outputs

    def on_cancel_clicked(self) -> None:
        logger.info("Cancel button clicked, aborting active tasks...")
        if self._pending_select_frames and not self._pending_select_frames.done() and self._select_frames_worker:
            self._select_frames_worker.cancel()
        if self._pending_extract_frames and not self._pending_extract_frames.done() and self._extract_frames_worker:
            self._extract_frames_worker.cancel()
        if self._pending_upload and not self._pending_upload.done():
            self._is_upload_cancelled = True
            self.slowpics_worker.cancel()
        self.progress_stack.hide()

    def on_url_copy_btn_clicked(self) -> None:
        QApplication.clipboard().setText(self._reported_url)
        QToolTip.showText(QCursor.pos(), "Copied!", self.url_copy_btn)
