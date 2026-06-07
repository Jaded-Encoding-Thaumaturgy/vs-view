from __future__ import annotations

from abc import abstractmethod
from collections import deque
from collections.abc import Callable, Generator
from concurrent.futures import Future, wait
from contextlib import contextmanager
from functools import partial
from logging import getLogger
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, ClassVar, Literal, assert_never, override

from jetpytools import clamp, fallback
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from vsengine.policy import ManagedEnvironment
from vsengine.vpy import ExecutionError, Script, load_code, load_script

from ...types import Frame
from ...vsenv import gc_collect, run_in_background, run_in_loop, unset_environment
from ..outputs import AudioOutput, OutputsManager, VideoOutput
from ..plugins.api import PluginAPI, WidgetPluginBase
from ..settings import ActionID, ShortcutManager
from ..views import PluginDock, PluginSplitter
from ..views.components import BlockableWidget, CustomLoadingPage, DockButton
from ..views.tab import TabViewWidget
from ..views.timeline import TimelineControlBar
from ..views.video import ViewState
from .base import BaseWorkspace
from .playback import PlaybackManager
from .tab_manager import PlayHeadToolButton, TabManager
from .utils import evict_packages, find_local_packages

loader_lock = Lock()
logger = getLogger(__name__)


class LoaderWorkspace[T](BaseWorkspace):
    """A workspace that supports loading content."""

    content: T
    """The content being loaded."""

    # Status bar signals
    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message
    statusLoadingErrored = Signal(str)  # error message
    statusOutputChanged = Signal(object)  # OutputInfo dataclass

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._is_failed = False

        self.stack = QStackedWidget(self)
        self.current_layout.addWidget(self.stack)

        # API & plugins
        self.api = PluginAPI(self)
        self.cbs_on_destroy = list[Callable[[], Any]]()
        self.plugins = list[WidgetPluginBase[Any, Any]]()
        self.docks = list[PluginDock]()
        self.plugins_loaded = False

        # Empty State
        self.empty_page = QWidget(self)
        self.empty_layout = QVBoxLayout(self.empty_page)
        self.empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.load_btn = QPushButton(f"Load {self.title}")
        self.load_btn.setFixedSize(200, 50)
        self.empty_layout.addWidget(self.load_btn)
        self.stack.addWidget(self.empty_page)

        # Error State (failed content with reload option)
        self.error_page = QWidget(self)
        self.error_layout = QHBoxLayout(self.error_page)
        self.error_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reload_btn = QPushButton(f"Reload {self.title}")
        self.reload_btn.setFixedSize(200, 50)
        self.reload_btn.clicked.connect(self._on_reload_failed)
        self.error_layout.addWidget(self.reload_btn)
        self.error_load_btn = QPushButton(f"Load {self.title}")
        self.error_load_btn.setFixedSize(200, 50)
        self.error_layout.addWidget(self.error_load_btn)
        self.stack.addWidget(self.error_page)

        # Loading State
        self.loading_page = CustomLoadingPage(self)
        self.stack.addWidget(self.loading_page)

        # Loaded State
        self.loaded_page = BlockableWidget(self)
        self.loaded_layout = QVBoxLayout(self.loaded_page)
        self.loaded_layout.setContentsMargins(0, 0, 0, 0)
        self.loaded_layout.setSpacing(0)

        # Horizontal container for toggle button and main content
        self.content_area = QWidget(self.loaded_page)
        self.content_layout = QHBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        # Left dock toggle button styled as a splitter handle
        self.dock_toggle_btn = DockButton(self.content_area)
        self.dock_toggle_btn.raise_()
        self.dock_toggle_btn.toggled.connect(self._on_dock_toggle)
        self.content_layout.addWidget(self.dock_toggle_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # Embedded QMainWindow for dock widget support in the view area
        self.dock_container = QMainWindow(self.content_area, documentMode=True)
        self.dock_container.setWindowFlags(Qt.WindowType.Widget)
        for area in (
            Qt.DockWidgetArea.LeftDockWidgetArea,
            Qt.DockWidgetArea.RightDockWidgetArea,
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        ):
            self.dock_container.setTabPosition(area, QTabWidget.TabPosition.North)

        self.plugin_splitter = PluginSplitter(self.dock_container)

        # Video Area (Tabs)
        self.tab_manager = TabManager(self.plugin_splitter, self.api)
        self.tab_manager.tabChanged.connect(self._on_tab_changed)
        self.tab_manager.sarTransformed.connect(lambda _: self._emit_output_info())
        self.plugin_splitter.insert_main_widget(self.tab_manager)

        # Connect plugin visibility signals
        self.tab_manager.toggle_tooldock_btn.toggled.connect(self._on_dock_toggle)
        self.tab_manager.toggle_toolpanel_btn.toggled.connect(self.plugin_splitter.toggle_right_panel)
        self.plugin_splitter.rightPanelVisibilityChanged.connect(self._sync_toolpanel_btn)
        self.plugin_splitter.rightPanelVisibilityChanged.connect(self._on_splitter_visibility_changed)
        self.plugin_splitter.pluginTabChanged.connect(self._on_splitter_tab_changed)

        self.dock_container.setCentralWidget(self.plugin_splitter)
        self.content_layout.addWidget(self.dock_container)

        self.loaded_layout.addWidget(self.content_area)

        # Timeline and Playback Controls
        self.tbar = TimelineControlBar(self)
        self.loaded_layout.addWidget(self.tbar)
        self.stack.addWidget(self.loaded_page)

        self.outputs_manager = OutputsManager()

        # PlaybackManager - handles video/audio playback logic
        self.playback = PlaybackManager(
            self.loop,
            lambda: self.env,
            self.api,
            self.outputs_manager,
            self.tab_manager,
            self.tbar,
            parent=self,
        )

        # Connect PlaybackManager signals to UI handlers
        self.playback.frameRendered.connect(self.tab_manager.update_current_view)
        self.playback.timelineCursorChanged.connect(self.update_timeline_cursor)
        self.playback.loadFailed.connect(self.clear_failed_load)

        # Audio control signals
        self.playback.audioOutputChanged.connect(
            lambda index: setattr(self.outputs_manager, "current_audio_index", index)
        )

        self._register_shortcuts()

    def _register_shortcuts(self) -> None:
        """Register workspace shortcuts with the shortcut manager."""
        sm = ShortcutManager()

        # Playback controls
        sm.register_shortcut(
            ActionID.PLAY_PAUSE,
            self.tbar.playback_container.play_pause_btn.click,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_PREVIOUS_FRAME,
            self.tbar.playback_container.seek_1_back_btn.click,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_NEXT_FRAME,
            self.tbar.playback_container.seek_1_fwd_btn.click,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_N_FRAMES_BACK,
            self.tbar.playback_container.seek_n_back_btn.click,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_N_FRAMES_FORWARD,
            self.tbar.playback_container.seek_n_fwd_btn.click,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_FIRST_FRAME,
            lambda: self.playback.request_frame(0),
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_LAST_FRAME,
            lambda: self.playback.request_frame(0x7FFFFFFF),
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.JUMP_TO_TIME,
            self.tbar.playback_container.time_edit.show_focus_for_user,
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.JUMP_TO_FRAME,
            self.tbar.playback_container.frame_edit.show_focus_for_user,
            self.loaded_page,
        )

        sm.register_shortcut(ActionID.RELOAD, self.reload_content, self.loaded_page)
        sm.register_shortcut(ActionID.RELOAD, self.reload_btn.click, self.error_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_FRAME, self._copy_current_frame_to_clipboard, self.loaded_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_TIME, self._copy_current_time_to_clipboard, self.loaded_page)

        for i, action in enumerate(act for act in ActionID if act.name.startswith("SWITCH_TAB_")):
            sm.register_shortcut(action, partial(self.tab_manager.switch_tab, i), self)

        sm.register_shortcut(ActionID.SWITCH_PREVIOUS_TAB, lambda: self.tab_manager.switch_tab(delta=-1), self)
        sm.register_shortcut(ActionID.SWITCH_NEXT_TAB, lambda: self.tab_manager.switch_tab(delta=1), self)

    @override
    def deleteLater(self) -> None:
        logger.debug(
            "%s(%r) deleteLater called, cleaning up resources",
            self.__class__.__name__,
            lambda: content.name if isinstance(content := getattr(self, "content", None), Path) else content,
        )

        self.playback.stop()
        self.playback.state.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))
        self.loop.wait_for_threads()

        return super().deleteLater()

    @override
    def clear_environment(self) -> None:
        if self._env and not self._env.disposed:
            with self._env.use():
                wait(self.loop.from_thread(cb) for cb in self.cbs_on_destroy)

        self.outputs_manager.clear()

        return super().clear_environment()

    @contextmanager
    def status_loading(self, loading_message: str, completed_message: str) -> Generator[None]:
        self.statusLoadingStarted.emit(loading_message)
        yield
        self.statusLoadingFinished.emit(completed_message)

    def get_output_metadata(self) -> dict[int, Any]:
        """
        Get metadata for VapourSynth outputs.

        Returns:
            A dictionary mapping output index to metadata string.
        """
        return {}

    @abstractmethod
    def loader(self) -> None: ...

    def init_load(self, frame: int | None = None, time: float | None = None, tab_index: int | None = None) -> None:
        from ..plugins.manager import PluginManager

        PluginManager.wait_for_loaded()

    @run_in_background(name="LoadContent")
    def load_content(
        self,
        content: T,
        /,
        frame: int | None = None,
        time: float | None = None,
        tab_index: int | None = None,
    ) -> None:
        logger.debug("load_content called: path=%r, frame=%r, tab_index=%r", content, frame, tab_index)

        self.set_loading_page()
        self.statusLoadingStarted.emit("Loading...")

        self.content = content

        unset_environment()
        self.init_load(frame, time, tab_index)

        outputs = self._get_outputs()

        if not outputs:
            self.clear_failed_load()
            return

        voutputs, aoutputs = outputs

        self.outputs_manager.current_video_index = clamp(self.outputs_manager.current_video_index, 0, len(voutputs) - 1)
        tabs = self.tab_manager.create_tabs(voutputs)

        with QSignalBlocker(self.tab_manager):
            self.tab_manager.swap_tabs(tabs, self.outputs_manager.current_video_index)

        self.tbar.playback_container.set_audio_outputs(aoutputs, self.outputs_manager.current_audio_index)

        # Load plugins in the load_content function so the plugins can get the file_path
        # and do VS things in the init since the environment is already created.
        self.load_plugins()

        @run_in_loop(return_future=False)
        def on_complete(f: Future[None]) -> None:
            if f.exception():
                logger.error("Failed to load content: %r", self.content)
                self.clear_failed_load()
                return

            self.content_area.setEnabled(True)
            self.tab_manager._on_global_autofit_changed(self.tab_manager.autofit_btn.isChecked())
            self.tab_manager.disable_switch = False
            self.playback.can_reload = True
            self._is_failed = False

            logger.info("Content loaded successfully: %r", self.content)
            self.statusLoadingFinished.emit("Completed")

        self._on_tab_changed(self.tab_manager.tabs.currentIndex(), cb_render=on_complete)

    @run_in_background(name="ReloadContent")
    def reload_content(self) -> None:
        if not self.playback.can_reload:
            logger.warning("Workspace is busy, cannot reload content")
            return

        if self.api.busy:
            logger.warning("At least one plugin is busy, cannot reload content")
            return

        logger.debug("Reloading content: %r", self.content)

        self.playback.stop()
        self.playback.can_reload = False
        self.statusLoadingStarted.emit("Reloading Content...")
        self.loop.from_thread(self.content_area.setDisabled, True).result()

        with self.tbar.disabled(), self.freeze_viewport():
            self.tab_manager.disable_switch = True
            self.playback.state.wait_for_cleanup(
                0.25,
                stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."),
            )
            self.loop.wait_for_threads()
            self.loop.next_cycle().result()

            # 1. Capture state
            saved_state, current_tab_i, autofit_enabled = self._capture_reload_ui_state()
            self._clear_reload_views()

            # 2. Reset Environment
            self.clear_environment()
            gc_collect()

            with loader_lock:
                # 2.5. Hot-reload local packages
                if local_packages := find_local_packages():
                    evict_packages(local_packages)
                    logger.info("Local packages reloaded: %r", sorted(local_packages))

            # 3. Load New Content
            outputs = self._get_outputs()

            if not outputs:
                self.clear_failed_load()
                return

            voutputs, aoutputs = outputs

            # 4. Reconstruct UI
            tabs = self.tab_manager.create_tabs(voutputs, enabled=False)
            current_tab_i = self._restore_reload_tabs(tabs, voutputs, saved_state, current_tab_i, autofit_enabled)

            self.tbar.playback_container.set_audio_outputs(aoutputs, self.outputs_manager.current_audio_index)

            @run_in_loop(return_future=False)
            def on_complete(f: Future[None]) -> None:
                if f.exception():
                    logger.error("Failed to reload content: %r", self.content)
                    self.clear_failed_load()
                    return
                self.content_area.setEnabled(True)
                self.tab_manager.tabs.setEnabled(True)
                self.content_area.setFocus()
                self.playback.can_reload = True
                self.tab_manager.disable_switch = False
                self._is_failed = False

                logger.info("Content reloaded successfully: %r", self.content)

            self._on_tab_changed(current_tab_i, seamless=True, cb_render=on_complete, refresh_plugins=True)

            logger.info("Content reloaded successfully: %r", self.content)

    @run_in_loop(return_future=False)
    def _capture_reload_ui_state(self) -> tuple[ViewState, int, bool]:
        return (
            self.tab_manager.current_view.state,
            self.tab_manager.tabs.currentIndex(),
            self.tab_manager.autofit_btn.isChecked(),
        )

    @run_in_loop(return_future=False)
    def _clear_reload_views(self) -> None:
        for view in self.tab_manager.tabs.views():
            view.clear_scene()

    @run_in_loop(return_future=False)
    def _restore_reload_tabs(
        self,
        tabs: TabViewWidget,
        voutputs: list[VideoOutput],
        saved_state: ViewState,
        current_tab_index: int,
        autofit_enabled: bool,
    ) -> int:
        for view, voutput in zip(tabs.views(), voutputs, strict=True):
            saved_state.apply_pixmap(view, (voutput.vs_output.clip.width, voutput.vs_output.clip.height))
            saved_state.apply_frozen_state(view)

        resolved_index = clamp(current_tab_index, 0, len(voutputs) - 1)

        with QSignalBlocker(self.tab_manager):
            self.tab_manager.swap_tabs(tabs, resolved_index)

        self.tab_manager._on_global_autofit_changed(autofit_enabled, under_reload=True)

        return resolved_index

    @contextmanager
    def freeze_viewport(self) -> Generator[None]:
        @run_in_loop(return_future=False)
        def show_screenshot() -> QLabel:
            viewport = self.tab_manager.current_view.viewport()

            screenshot = QLabel(self)
            screenshot.setPixmap(viewport.grab())  # Grab just the viewport, no frame/scrollbars

            pos = viewport.mapTo(self, viewport.rect().topLeft())
            screenshot.setGeometry(pos.x(), pos.y(), viewport.width(), viewport.height())
            screenshot.raise_()
            screenshot.show()
            return screenshot

        screenshot = show_screenshot()

        try:
            yield
        finally:
            self.loop.from_thread(screenshot.setVisible, False)
            self.loop.from_thread(screenshot.deleteLater)
            del screenshot

    @run_in_loop(return_future=False)
    def clear_failed_load(self) -> None:
        if self._is_failed:
            logger.debug("Workspace already in failed state, skipping clear_failed_load...")
            return

        self.playback.stop()
        self.playback.state.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

        with QSignalBlocker(self.tab_manager.tabs):
            self.tab_manager.tabs.clear()

        self.clear_environment()

        self.statusLoadingErrored.emit("Error while loading content")
        self.set_error_page()
        gc_collect()

        self._is_failed = True

    @run_in_loop
    def init_timeline(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            logger.debug("No voutput available")
            return

        fps = voutput.vs_output.clip.fps
        total_frames = voutput.vs_output.clip.num_frames

        self.tbar.timeline.reset_interaction()
        self.tbar.set_data(total_frames, voutput.cum_durations)

        # Use configured FPS history size, or auto-calculate from FPS when set to 0
        if (fps_history_size := self.global_settings.playback.fps_history_size) <= 0:
            fps_history_size = (
                round(fps)
                if fps > 0
                else round(1 / (sum(voutput.framedurs) / len(voutput.framedurs)))
                if voutput.framedurs
                else 25
            )

        self.playback.state.fps_history = deque(maxlen=clamp(fps_history_size, 1, total_frames))

    @run_in_loop
    def update_timeline_cursor(self, n: int) -> None:
        if not self.outputs_manager.current_voutput:
            return

        self.tbar.timeline.cursor_x = (n := Frame(n))

        with QSignalBlocker(self.tbar.playback_container.frame_edit):
            self.tbar.playback_container.frame_edit.setValue(n)

        with QSignalBlocker(self.tbar.playback_container.time_edit):
            time = self.outputs_manager.current_voutput.frame_to_time(n)
            self.tbar.playback_container.time_edit.setTime(time.to_qtime())

    @run_in_loop(return_future=False)
    def set_loaded_page(self) -> None:
        self.stack.setCurrentWidget(self.loaded_page)
        self.content_area.setFocus()

    @run_in_loop(return_future=False)
    def set_loading_page(self) -> None:
        logger.debug("Switching to loading page")
        self._is_failed = False
        self.stack.setCurrentWidget(self.loading_page)

    @run_in_loop(return_future=False)
    def set_empty_page(self) -> None:
        self.stack.setCurrentWidget(self.empty_page)

    @run_in_loop(return_future=False)
    def set_error_page(self) -> None:
        self.stack.setCurrentWidget(self.error_page)

    def _get_outputs(self) -> tuple[list[VideoOutput], list[AudioOutput]] | None:
        try:
            with self.env.use():
                self.loader()

                voutputs = self.outputs_manager.create_voutputs(
                    self.content,
                    self.video_outputs,
                    self.get_output_metadata(),
                    self.api,
                    last_frame=self.playback.state.current_frame,
                )

                if not voutputs:
                    raise RuntimeError

                aoutputs = self.outputs_manager.create_aoutputs(
                    self.content,
                    self.audio_outputs,
                    self.get_output_metadata(),
                    self.api,
                    delay_s=self.tbar.playback_container.audio_delay,
                )

                return voutputs, aoutputs
        except Exception:
            logger.debug("Full traceback:", exc_info=True)
            return None

    def _on_tab_changed(
        self,
        index: int,
        seamless: bool = False,
        cb_render: Callable[[Future[None]], None] | None = None,
        refresh_plugins: bool = False,
    ) -> bool:
        self.playback.stop()
        self.outputs_manager.current_video_index = index

        if not self.outputs_manager.current_voutput:
            logger.debug("Invalid tab index %d, ignoring", index)
            return True

        logger.debug("Switching to video output: clip=%r", self.outputs_manager.current_voutput.vs_output.clip)

        target_frame = self._calculate_target_frame()
        self.init_timeline()
        self.update_timeline_cursor(target_frame)
        self._emit_output_info()

        previous_voutput = self.outputs_manager.voutputs[self.tab_manager.tabs.previous_tab_index]
        current_voutput = self.outputs_manager.current_voutput

        timer_disable = None
        if (
            not (previous_voutput.last_frame == current_voutput.last_frame == target_frame)
            or not current_voutput.loaded_once
        ):
            current_voutput.loaded_once = True
            if not seamless:
                if (prev_pixmap := self.tab_manager.previous_view.pixmap_item.pixmap()).isNull():
                    self.set_loading_page()
                else:
                    # Disable the loaded page if the frame takes too much time (50 ms) to render
                    self.loaded_page.blocked = True
                    timer_disable = QTimer(self, singleShot=True, timerType=Qt.TimerType.PreciseTimer)
                    timer_disable.timeout.connect(lambda: self.loaded_page.setDisabled(True))
                    timer_disable.start(50)
                    self.tab_manager.update_current_view(prev_pixmap)

            def on_complete(f: Future[None]) -> None:
                if not f.exception():
                    if timer_disable:
                        timer_disable.stop()
                    self.loaded_page.setEnabled(True)
                    self.set_loaded_page()

                    with self.env.use():
                        self.api._on_current_voutput_changed(refresh_plugins)

                if cb_render:
                    cb_render(f)

            logger.debug("Requesting frame %d", target_frame)
            try:
                self.playback.request_frame(target_frame, on_complete)
            except Exception:
                return False
        else:
            with self.env.use():
                self.api._on_current_voutput_changed(refresh_plugins)

        return True

    def _calculate_target_frame(self, state: PlayHeadToolButton.State | None = None) -> int:
        if not (voutput := self.outputs_manager.current_voutput):
            raise NotImplementedError

        match s := fallback(state, self.tab_manager.sync_playhead_state):
            case PlayHeadToolButton.State.UNLINK:
                target_frame = voutput.last_frame
                logger.debug("Sync playhead %r, using last frame %d", s, target_frame)
                return target_frame
            case PlayHeadToolButton.State.LINK_TIME:
                target_frame = clamp(
                    self.outputs_manager.current_voutput.time_to_frame(self.playback.state.current_time),
                    0,
                    self.outputs_manager.current_voutput.vs_output.clip.num_frames - 1,
                )

                logger.debug(
                    "Sync playhead %r, targeting frame %d (from time %.3fs)",
                    s,
                    target_frame,
                    self.playback.state.current_time.total_seconds(),
                )
                return target_frame
            case PlayHeadToolButton.State.LINK_FRAME:
                target_frame = clamp(
                    self.playback.state.current_frame,
                    0,
                    self.outputs_manager.current_voutput.vs_output.clip.num_frames - 1,
                )
                logger.debug(
                    "Sync playhead %r, targeting frame %d (from frame %d)",
                    s,
                    target_frame,
                    self.playback.state.current_frame,
                )
                return target_frame
            case PlayHeadToolButton.State.LINK_ADAPT as s:
                logger.debug("Sync playhead %r", s)
                resolved_state = {
                    "frame": PlayHeadToolButton.State.LINK_FRAME,
                    "time": PlayHeadToolButton.State.LINK_TIME,
                }[self.tbar.timeline.mode]
                return self._calculate_target_frame(resolved_state)
            case _:
                assert_never(s)

    def _emit_output_info(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            logger.warning("No current video output, ignoring")
            return

        self.statusOutputChanged.emit(voutput.info._replace(sar=self.tab_manager.current_view.display_sar))

    def _on_reload_failed(self) -> None:
        self.load_content(self.content)

    def _copy_current_frame_to_clipboard(self) -> None:
        frame = self.tbar.playback_container.frame_edit.value()

        QApplication.clipboard().setText(str(frame))

        self.statusLoadingFinished.emit(f"Copied frame {frame}")
        logger.info("Copied frame %d to clipboard", frame)

    def _copy_current_time_to_clipboard(self) -> None:
        timestamp = self.tbar.playback_container.time_edit.time().toString("H:mm:ss.zzz")

        QApplication.clipboard().setText(timestamp)

        self.statusLoadingFinished.emit(f"Copied time {timestamp}")
        logger.info("Copied time %s to clipboard", timestamp)

    @run_in_loop(return_future=False)
    def load_plugins(self) -> None:
        if not self.plugins_loaded:
            self.plugins.clear()
            self.docks.clear()

            with self.env.use():
                self._setup_docks()
                self._setup_panels()

            self.plugins_loaded = True

    def _setup_docks(self) -> None:
        from ..plugins.manager import PluginManager

        for plugin_type in PluginManager.tooldocks:
            dock = PluginDock(plugin_type.display_name, plugin_type.identifier, self.dock_container)
            plugin_obj = plugin_type(dock, self.api)

            dock.setWidget(plugin_obj)
            dock.visibilityChanged.connect(lambda visible, d=dock: self._on_dock_visibility_changed(visible, d))

            self.plugins.append(plugin_obj)
            self.docks.append(dock)

            self.dock_container.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

            if len(self.docks) > 1:
                self.dock_container.tabifyDockWidget(self.docks[0], dock)

        # Docks are hidden by default, so toggle button starts unchecked
        self.dock_toggle_btn.setChecked(False)

    def _setup_panels(self) -> None:
        from ..plugins.manager import PluginManager

        for i, plugin_type in enumerate(PluginManager.toolpanels):
            plugin_obj = plugin_type(self.plugin_splitter.plugin_tabs, self.api)

            self.plugins.append(plugin_obj)
            self.plugin_splitter.add_plugin(plugin_obj, plugin_type.display_name)
            self.plugin_splitter.plugin_tabs.setTabVisible(
                i, self.global_settings.view_tools.panels.get(plugin_type.identifier, True)
            )

    def _on_dock_toggle(self, checked: bool) -> None:
        for dock in self.docks:
            if self.global_settings.view_tools.docks.get(dock.objectName(), True):
                dock.setVisible(checked)

        with QSignalBlocker(self.dock_toggle_btn), QSignalBlocker(self.tab_manager.toggle_tooldock_btn):
            self.dock_toggle_btn.setChecked(checked)
            self.tab_manager.toggle_tooldock_btn.setChecked(checked)

    def _on_dock_visibility_changed(self, visible: bool, dock: PluginDock) -> None:
        if not isinstance(w := dock.widget(), WidgetPluginBase):
            return

        if visible:
            with self.env.use():
                self.api._init_plugin(w)

            dock.truly_visible = True
        elif dock.truly_visible:
            w.on_hide()
            dock.truly_visible = False

    def _sync_toolpanel_btn(self, visible: bool) -> None:
        with QSignalBlocker(self.tab_manager.toggle_toolpanel_btn):
            self.tab_manager.toggle_toolpanel_btn.setChecked(visible)

    def _on_splitter_visibility_changed(self, visible: bool = True) -> None:
        if not isinstance(w := self.plugin_splitter.plugin_tabs.currentWidget(), WidgetPluginBase):
            return

        if visible:
            if self.outputs_manager.current_voutput:
                with self.env.use():
                    self.api._init_plugin(w)
        else:
            w.on_hide()

    def _on_splitter_tab_changed(self, new_index: int, old_index: int) -> None:
        if isinstance(w := self.plugin_splitter.plugin_tabs.widget(new_index), WidgetPluginBase):
            with self.env.use():
                self.api._init_plugin(w)

        if isinstance(w := self.plugin_splitter.plugin_tabs.widget(old_index), WidgetPluginBase):
            w.on_hide()


class VSEngineWorkspace[T](LoaderWorkspace[T]):
    """Base workspace for script execution."""

    content_type: ClassVar[Literal["script", "code"]]
    """The type of content to load."""

    script: Script[ManagedEnvironment]
    """The loaded script. Available only after loader() is called."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vsargs = dict[str, Any]()

    @property
    def _script_content(self) -> Any:
        """Return the content to be loaded by the script engine."""
        return self.content

    @property
    def _script_kwargs(self) -> dict[str, Any]:
        """Return additional keyword arguments for vsengine.vpy.load_{content_type}()."""
        return {}

    @property
    def _user_script_path(self) -> str:
        """Return the user script path/filename for error reporting."""
        return (
            str(self._script_content)
            if self.content_type == "script"
            else self._script_kwargs.get("filename", repr(self.content))
        )

    @override
    def loader(self) -> None:
        module = ModuleType("__vsview__")
        module.__dict__.update(self.vsargs)

        match self.content_type:
            case "script":
                chdir = Path(self._user_script_path).parent if self.global_settings.chdir else None
                self.script = load_script(
                    self._script_content,
                    self.env,
                    module=module,
                    chdir=chdir,
                    **self._script_kwargs,
                )
            case "code":
                self.script = load_code(self._script_content, self.env, module=module, **self._script_kwargs)
            case _:
                assert_never(self.content_type)

        logger.debug("Running Script...")

        fut = self.script.run()

        try:
            fut.result()
            logger.debug("%s execution completed successfully", self.content_type.title())
        except ExecutionError as e:
            from ...app.error import show_error

            self.statusLoadingErrored.emit("Execution error")

            show_error(e, self, self._user_script_path)
            # Clear traceback to release VS core references held in the exception chain
            e.parent_error.__traceback__ = None
            e.__traceback__ = None

            raise RuntimeError("Script execution failed") from None
