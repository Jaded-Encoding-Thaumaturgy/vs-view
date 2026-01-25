from abc import abstractmethod
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from logging import getLogger
from pathlib import Path
from threading import Lock
from time import perf_counter_ns
from types import ModuleType
from typing import Any, ClassVar, Literal, assert_never

from jetpytools import clamp, cround
from PySide6.QtCore import QSignalBlocker, Qt, QTime, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from vsengine.policy import ManagedEnvironment
from vsengine.vpy import ExecutionError, Script, load_code, load_script

from ...vsenv import clear_environment, gc_collect, run_in_background, run_in_loop, unset_environment
from ..outputs import FrameBuffer, VideoOutput, get_packer
from ..plugins.api import PluginAPI, PluginBase
from ..plugins.manager import PluginManager
from ..settings import ActionID, ShortcutManager
from ..views import OutputInfo, PluginSplitter
from ..views.components import CustomLoadingPage, DockButton
from ..views.timeline import Frame, Time, TimelineControlBar
from .base import BaseWorkspace
from .tab_manager import TabManager

loader_lock = Lock()
logger = getLogger(__name__)

# Module constants
FPS_UPDATE_INTERVAL_NS = 1_000_000_000  # 1 second
MIN_FRAME_DELAY_NS = 1_000_000  # 1ms minimum scheduling delay


@dataclass(slots=True, repr=False, eq=False, match_args=False)
class PlaybackState:
    """Manages playback-related state."""

    last_fps_update_ns: int = 0
    """Timestamp (ns) of last FPS display update."""

    frame_interval_ns: int = 0
    """Target frame interval in nanoseconds for FPS limiting."""

    next_frame_time_ns: int = 0
    """Target time (ns) when next frame should start."""

    pending_frame: int = 0
    """Next frame to render when timer fires."""

    fps_history: deque[int] = field(default_factory=lambda: deque(maxlen=25))
    """Rolling window of frame timestamps for FPS averaging."""

    buffer: FrameBuffer | None = None
    """Frame buffer for async pre-fetching during playback."""

    stop_at_frame: int | None = None
    """Frame number to stop playback at."""

    _cleanup_future: Future[None] | None = field(default=None, init=False)
    """Pending buffer cleanup future."""

    def reset(self) -> None:
        self.last_fps_update_ns = 0
        self.frame_interval_ns = 0
        self.next_frame_time_ns = 0
        self.pending_frame = 0
        self.stop_at_frame = None
        self.fps_history.clear()

        if self.buffer:
            self._cleanup_future = self.buffer.invalidate()
            self.buffer = None

    def wait_for_cleanup(self, timeout: float | None = None, stall_cb: Callable[[], None] | None = None) -> None:
        if self._cleanup_future:
            if timeout is not None and stall_cb:
                _, undone = wait([self._cleanup_future], timeout=timeout)

                if undone:
                    stall_cb()

            self._cleanup_future.result()
            self._cleanup_future = None


class LoaderWorkspace[T](BaseWorkspace):
    """A workspace that supports loading content."""

    content: T
    """The content being loaded."""

    # Status bar signals
    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message
    statusLoadingErrored = Signal(str)  # error message
    statusOutputChanged = Signal(object)  # OutputInfo dataclass
    workspacePluginsLoaded = Signal()  # emitted when plugin instances are created

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.stack = QStackedWidget(self)
        self.current_layout.addWidget(self.stack)

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
        self.loaded_page = QWidget(self)
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
        self.dock_toggle_btn.clicked.connect(self._on_dock_toggle)
        self.content_layout.addWidget(self.dock_toggle_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # Embedded QMainWindow for dock widget support in the view area
        self.dock_container = QMainWindow(self.content_area)
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
        self.tab_manager = TabManager(self.plugin_splitter)
        self.tab_manager.tabChanged.connect(self._on_tab_changed)
        self.plugin_splitter.insert_main_widget(self.tab_manager)

        # Connect plugin visibility signals
        self.plugin_splitter.rightPanelBecameVisible.connect(self._init_visible_plugins)
        self.plugin_splitter.pluginTabChanged.connect(lambda _: self._init_visible_plugins())

        # Connect dock tab activation signal for tabified docks
        self.dock_container.tabifiedDockWidgetActivated.connect(lambda _: self._init_visible_plugins())

        self.dock_container.setCentralWidget(self.plugin_splitter)
        self.content_layout.addWidget(self.dock_container)

        self.loaded_layout.addWidget(self.content_area)

        # Timeline and Playback Controls
        self.tbar = TimelineControlBar(self)
        self.tbar.timeline.clicked.connect(self._on_timeline_clicked)

        # Seek buttons
        self.tbar.playback_container.seek_1_back_btn.clicked.connect(lambda: self._seek_frame(-1))
        self.tbar.playback_container.play_pause_btn.clicked.connect(self._toggle_playback)
        self.tbar.playback_container.seek_1_fwd_btn.clicked.connect(lambda: self._seek_frame(1))
        self.tbar.playback_container.seek_n_back_btn.clicked.connect(lambda: self._seek_n_frames(-1))
        self.tbar.playback_container.seek_n_fwd_btn.clicked.connect(lambda: self._seek_n_frames(1))

        # Seek step context menu signals
        self.tbar.playback_container.seekStepReset.connect(self._on_seek_step_reset)
        self.tbar.playback_container.settingsChanged.connect(self._on_playback_settings_changed)
        self.tbar.playback_container.playZone.connect(self._on_play_zone)

        self.loaded_layout.addWidget(self.tbar)

        # Connect Frame/Time Edit signals
        self.tbar.playback_container.frame_edit.frameChanged.connect(self._on_frame_changed)
        self.tbar.playback_container.time_edit.valueChanged.connect(self._on_time_changed)

        self.stack.addWidget(self.loaded_page)

        # Playback state
        self._playback = PlaybackState()
        self.tbar.playback_timer.timeout.connect(self._on_playback_timer)

        # Reloading state
        self.disable_reloading = True

        # API & plugins
        self.api = PluginAPI(self)
        self.cbs_on_destroy = list[Callable[[], Any]]()
        self.plugins = list[PluginBase]()
        self.docks = list[QDockWidget]()
        self.plugins_loaded = False

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
        sm.register_shortcut(ActionID.RELOAD, self.reload_content, self.loaded_page)
        sm.register_shortcut(ActionID.RELOAD, self.reload_btn.click, self.error_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_FRAME, self._copy_current_frame_to_clipboard, self.loaded_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_TIME, self._copy_current_time_to_clipboard, self.loaded_page)

        tab_actions = (
            ActionID.SWITCH_TAB_0,
            ActionID.SWITCH_TAB_1,
            ActionID.SWITCH_TAB_2,
            ActionID.SWITCH_TAB_3,
            ActionID.SWITCH_TAB_4,
            ActionID.SWITCH_TAB_5,
            ActionID.SWITCH_TAB_6,
            ActionID.SWITCH_TAB_7,
            ActionID.SWITCH_TAB_8,
            ActionID.SWITCH_TAB_9,
        )
        for i, action in enumerate(tab_actions):
            sm.register_shortcut(action, partial(self.tab_manager.switch_tab, i), self)

    @property
    def current_frame(self) -> int:
        return getattr(self, "_current_frame", 0)

    @current_frame.setter
    def current_frame(self, value: int) -> None:
        self._current_frame = value

    @property
    def current_tab_index(self) -> int:
        return getattr(self, "_current_tab_index", 0)

    @current_tab_index.setter
    def current_tab_index(self, value: int) -> None:
        self._current_tab_index = value

    def deleteLater(self) -> None:
        logger.debug(
            "%s(%r) deleteLater called, cleaning up resources",
            self.__class__.__name__,
            lambda: content.name if isinstance(content := getattr(self, "content", None), Path) else content,
        )

        self._stop_playback()

        self.tab_manager.deleteLater()

        return super().deleteLater()

    def clear_environment(self) -> None:
        if self.cbs_on_destroy:

            @run_in_loop(return_future=False)
            def destroy() -> None:
                with self.env.use():
                    for cb in self.cbs_on_destroy:
                        cb()

            destroy()
        return super().clear_environment()

    @contextmanager
    def status_loading(self, loading_message: str, completed_message: str) -> Iterator[None]:
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

    def create_voutputs(self) -> list[VideoOutput]:
        """
        Create VideoOutput wrappers for all video outputs.

        Returns an empty list on error; caller is responsible for cleanup.
        """
        voutputs = list[VideoOutput]()
        video_outputs = self.video_outputs
        metadata = self.get_output_metadata()

        self._packer = get_packer(self.global_settings.view.packing_method, self.global_settings.view.bit_depth)
        logger.debug("Configured video packer: %s (%s-bit)", self._packer.name, self._packer.bit_depth)

        if not video_outputs:
            logger.error("No video outputs found")

        # Snapshot items to avoid keeping the dict iterator alive during processing.
        # This prevents the iterator from holding references to VS objects in the traceback.
        items = list(video_outputs.items())

        try:
            for i, output in items:
                voutputs.append(VideoOutput(output, i, self._packer, metadata.get(i)))
        except Exception:
            for voutput in voutputs:
                voutput.clear()
            logger.exception("Failed to load script: %r", self.content)
            return []

        return voutputs

    @abstractmethod
    def loader(self) -> None: ...

    def init_load(self, frame: int | None = None, tab_index: int | None = None) -> None: ...

    @run_in_background(name="LoadContent")
    def load_content(self, content: T, /, frame: int | None = None, tab_index: int | None = None) -> None:
        logger.debug("load_content called: path=%r, frame=%r, tab_index=%r", content, frame, tab_index)

        self.set_loading_page()
        self.statusLoadingStarted.emit("Loading...")

        self.content = content

        with loader_lock:
            unset_environment()
            self.env.switch()
            self.init_load(frame, tab_index)

            # Handle user error
            try:
                self.loader()
                if not (voutputs := self.create_voutputs()):
                    raise RuntimeError
            except Exception:
                self.clear_failed_load()
                return

        tabs = self.tab_manager.create_tabs(voutputs, self.current_frame)

        with QSignalBlocker(self.tab_manager):
            self.tab_manager.swap_tabs(tabs, self.current_tab_index)

        # Load plugins in the load_content function so the plugins can get the file_path
        # and do VS things in the init since the environment is already created.
        if PluginManager.loaded:
            self.load_plugins()
        else:
            PluginManager.signals.pluginsLoaded.connect(self.load_plugins)

        @run_in_loop(return_future=False)
        def on_complete(f: Future[None]) -> None:
            if f.exception():
                return

            self.content_area.setEnabled(True)
            self.tab_manager._on_global_autofit_changed(self.tab_manager.autofit_btn.isChecked())
            self.tab_manager.disable_switch = False
            self.disable_reloading = False

        # Handle potiential error on frame rendering
        try:
            self._on_tab_changed(self.current_tab_index, cb_render=on_complete)
        except Exception:
            logger.error("Failed to load content: %r", self.content)
            self.clear_failed_load()
            return

        logger.info("Content loaded successfully: %r", self.content)
        self.statusLoadingFinished.emit("Completed")

    @run_in_background(name="ReloadContent")
    def reload_content(self) -> None:
        if self.disable_reloading:
            logger.warning("Workspace is busy, cannot reload content")
            return

        logger.debug("Reloading content: %r", self.content)

        self._stop_playback()
        self.disable_reloading = True
        self.statusLoadingStarted.emit("Reloading Content...")

        with self.tbar.disabled(), self.tab_manager.clear_voutputs_on_fail():
            self.loop.from_thread(self.content_area.setDisabled, True)
            self.tab_manager.disable_switch = True
            self._playback.wait_for_cleanup(0.25, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

            # 1. Capture and Preserve State
            saved_state = self.tab_manager.current_view.state

            @run_in_loop(return_future=False)
            def preserve_ui() -> None:
                self.tab_manager.current_view.pixmap_item.setPixmap(saved_state.pixmap)
                for view in self.tab_manager.tabs.views():
                    if view is not self.tab_manager.current_view:
                        view.clear_scene()

            # Clear old references
            for vo in self.tab_manager.voutputs.values():
                vo.clear()

            preserve_ui()

            # 2. Reset Environment
            self.clear_environment()
            gc_collect()

            # 3. Load New Content
            with loader_lock:
                self.env.switch()
                try:
                    self.loader()
                    if not (voutputs := self.create_voutputs()):
                        raise RuntimeError
                except Exception:
                    self.clear_failed_load()
                    return

            # 4. Reconstruct UI
            tabs = self.tab_manager.create_tabs(voutputs, self.current_frame, enabled=False)

            # Apply saved pixmap
            for view, voutput in zip(tabs.views(), voutputs, strict=True):
                saved_state.apply_pixmap(view, (voutput.clip.width, voutput.clip.height))

            with QSignalBlocker(self.tab_manager):
                self.tab_manager.swap_tabs(tabs, self.tab_manager.tabs.currentIndex())

            saved_state.apply_frozen_state(self.tab_manager.current_view)

            self.loop.from_thread(
                self.tab_manager._on_global_autofit_changed,
                self.tab_manager.autofit_btn.isChecked(),
            ).result()

            @run_in_loop(return_future=False)
            def on_complete(f: Future[None]) -> None:
                if f.exception():
                    return
                saved_state.restore_view_state(self.tab_manager.current_view)
                self.content_area.setEnabled(True)
                self.tab_manager.tabs.setEnabled(True)
                self.disable_reloading = False
                self.tab_manager.disable_switch = False

            try:
                self._on_tab_changed(self.tab_manager.tabs.currentIndex(), seamless=True, cb_render=on_complete)
            except Exception:
                logger.error("Failed to reload content: %r", self.content)
                self.clear_failed_load()
                raise

            logger.info("Content reloaded successfully: %r", self.content)

    @run_in_loop(return_future=False)
    def clear_failed_load(self) -> None:
        self._stop_playback()
        self._playback.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

        with QSignalBlocker(self.tab_manager.tabs):
            self.tab_manager.tabs.clear()

        self.clear_environment()

        self.statusLoadingErrored.emit("Error while loading content")
        self.set_error_page()
        gc_collect()

    @run_in_loop
    def request_frame(self, n: int, cb_render: Callable[[Future[None]], None] | None = None) -> None:
        """Request a specific frame to be rendered and displayed."""
        logger.debug("Frame requested: %d", n)

        fut = self._render_frame(n)

        def on_complete(f: Future[None]) -> None:
            if f.exception():
                logger.exception("Resize failed with the message:")
                self.clear_failed_load()
            elif self.tab_manager.tabs.currentIndex() != -1:
                self.current_frame = n
                self.tab_manager.current_view.last_frame = n

        fut.add_done_callback(on_complete)

        if cb_render:
            fut.add_done_callback(cb_render)

    @run_in_background(name="RenderFrame")
    def _render_frame(self, n: int) -> None:
        """Render a specific frame and update the view."""
        logger.debug("Rendering frame %d (background)", n)

        if not (voutput := self.tab_manager.current_voutput):
            return

        self.disable_reloading = True

        with self.env.use():
            if self.tbar.is_playing:
                with voutput.prepared_clip.get_frame(n) as frame:
                    logger.debug("Frame %d rendered", n)
                    image = voutput.packer.frame_to_qimage(frame)

                self.api._on_current_frame_changed(n, None)
            else:
                self.statusLoadingStarted.emit(f"Rendering frame {n}...")

                with self.status_loading(f"Rendering frame {n}...", "Completed"):
                    with self.tbar.disabled(), voutput.prepared_clip.get_frame(n) as frame:
                        logger.debug("Frame %d rendered", n)
                        image = self._packer.frame_to_qimage(frame)

                    self.api._on_current_frame_changed(n, None)

        self.tab_manager.update_current_view(image, skip_adjustments=self.disable_reloading)
        self.update_timeline_cursor(n)
        self.disable_reloading = False

    @run_in_loop
    def init_timeline(self) -> None:
        if not (voutput := self.tab_manager.current_voutput):
            logger.debug("No voutput available")
            return

        fps = voutput.clip.fps
        total_frames = voutput.clip.num_frames

        self.tbar.timeline.set_data(total_frames, fps)

        # Use configured FPS history size, or auto-calculate from FPS when set to 0
        if (fps_history_size := self.global_settings.view.fps_history_size) <= 0:
            fps_history_size = round(fps.numerator / fps.denominator)

        self._playback.fps_history = deque(maxlen=clamp(fps_history_size, 1, total_frames))

        with QSignalBlocker(self.tbar.playback_container.frame_edit):
            self.tbar.playback_container.frame_edit.setMaximum(Frame(total_frames - 1))

        with QSignalBlocker(self.tbar.playback_container.time_edit):
            self.tbar.playback_container.time_edit.setMaximumTime(self.tbar.timeline.total_time.to_qtime())

        self.tbar.playback_container.fps = fps

    @run_in_loop
    def update_timeline_cursor(self, n: int) -> None:
        if not self.tab_manager.current_voutput:
            return

        self.tbar.timeline.cursor_x = (n := Frame(n))

        with QSignalBlocker(self.tbar.playback_container.frame_edit):
            self.tbar.playback_container.frame_edit.setValue(n)

        with QSignalBlocker(self.tbar.playback_container.time_edit):
            time = self.tab_manager.current_voutput.frame_to_time(n)
            self.tbar.playback_container.time_edit.setTime(time.to_qtime())

    @run_in_loop(return_future=False)
    def set_loaded_page(self) -> None:
        self.stack.setCurrentWidget(self.loaded_page)

    @run_in_loop(return_future=False)
    def set_loading_page(self) -> None:
        logger.debug("Switching to loading page")
        self.stack.setCurrentWidget(self.loading_page)

    @run_in_loop(return_future=False)
    def set_empty_page(self) -> None:
        self.stack.setCurrentWidget(self.empty_page)

    @run_in_loop(return_future=False)
    def set_error_page(self) -> None:
        self.stack.setCurrentWidget(self.error_page)

    def _on_dock_toggle(self, checked: bool) -> None:
        for dock in self.docks:
            if self.global_settings.view_tools.docks.get(dock.objectName(), True):
                dock.setVisible(checked)

    def _init_visible_plugins(self) -> None:
        if not self.tab_manager.current_voutput:
            return  # No content loaded yet

        with self.env.use():
            for plugin in self.plugins:
                self.api._init_plugin(plugin)

    def _on_tab_changed(
        self, index: int, seamless: bool = False, cb_render: Callable[[Future[None]], None] | None = None
    ) -> None:
        if not self.tab_manager.current_voutput:
            logger.debug("Invalid tab index %d, ignoring", index)
            return

        self._stop_playback()
        self._playback.wait_for_cleanup()

        logger.debug("Switched to video output: clip=%r", self.tab_manager.current_voutput.clip)

        self.init_timeline()
        target_frame = self._calculate_target_frame()
        self.update_timeline_cursor(target_frame)
        self.current_tab_index = index
        self._emit_output_info()

        if (
            not (self.tab_manager.previous_view.last_frame == self.tab_manager.current_view.last_frame == target_frame)
            or not self.tab_manager.current_view.loaded_once
        ):
            if not seamless:
                self.set_loading_page()
            self.tab_manager.current_view.loaded_once = True

            def on_complete(f: Future[None]) -> None:
                if not f.exception():
                    self.set_loaded_page()

                    if cb_render:
                        cb_render(f)

                    with self.env.use():
                        self.api._on_current_voutput_changed()

            logger.debug("Requesting frame %d", target_frame)
            self.request_frame(target_frame, on_complete)
        else:
            with self.env.use():
                self.api._on_current_voutput_changed()

    def _calculate_target_frame(self) -> int:
        if not self.tab_manager.is_sync_playhead_enabled:
            logger.debug(
                "Sync playhead disabled, using last frame %d",
                (target_frame := self.tab_manager.current_view.last_frame),
            )
            return target_frame

        assert self.tab_manager.current_voutput

        src_fps = self.tab_manager.previous_view.output.clip.fps
        tgt_fps = self.tab_manager.current_voutput.clip.fps

        current_time = self.tab_manager.current_voutput.frame_to_time(self.current_frame, src_fps)
        target_frame = self.tab_manager.current_voutput.time_to_frame(current_time, tgt_fps)

        target_frame = clamp(target_frame, 0, self.tab_manager.current_voutput.clip.num_frames - 1)

        logger.debug(
            "Sync playhead enabled, targeting frame %d (from time %.3fs)",
            target_frame,
            current_time.total_seconds(),
        )
        return target_frame

    @Slot(int)
    def _seek_frame(self, delta: int) -> None:
        if not self.tab_manager.current_voutput:
            logger.warning("No current video output, ignoring")
            return

        new_frame = clamp(self.current_frame + delta, 0, self.tab_manager.current_voutput.clip.num_frames - 1)
        self.request_frame(new_frame)

    @Slot(int)
    def _seek_n_frames(self, direction: int) -> None:
        self._seek_frame(direction * self.tbar.playback_container.settings.seek_step)

    def _on_playback_settings_changed(self, seek_step: int, speed: float, uncapped: bool) -> None:
        if self.tbar.is_playing:
            self._restart_playback()

    def _on_play_zone(self, zone_frames: int, loop: bool) -> None:
        if not self.tab_manager.current_voutput:
            return

        start_frame = self.current_frame
        # Ensure zone doesn't exceed total frames
        total_frames = self.tab_manager.current_voutput.clip.num_frames
        end_frame = min(start_frame + zone_frames, total_frames)

        loop_range = range(start_frame, end_frame) if loop else None
        stop_at = end_frame if not loop else None

        self.tbar.is_playing = True
        self.tbar.playback_container.play_pause_btn.setChecked(True)
        self.tbar.set_playback_controls_enabled(False)
        self.tbar.timeline.is_events_blocked = True

        self._start_playback(loop_range=loop_range, stop_at=stop_at)

    def _on_seek_step_reset(self) -> None:
        self.tbar.playback_container.settings.seek_step = self.global_settings.timeline.seek_step

    def _restart_playback(self) -> None:
        self._stop_playback()
        self.tbar.is_playing = True
        self.tbar.playback_container.play_pause_btn.setChecked(True)
        self._start_playback()

    def _emit_output_info(self) -> None:
        if not (voutput := self.tab_manager.current_voutput):
            logger.warning("No current video output, ignoring")
            return

        # Calculate total duration
        if voutput.clip.fps.numerator > 0:
            total_seconds = voutput.clip.num_frames * voutput.clip.fps.denominator / voutput.clip.fps.numerator
            total_duration = Time(seconds=total_seconds).to_ts("{H}:{M:02d}:{S:02d}.{ms:03d}")
            fps_str = f"{voutput.clip.fps.numerator / voutput.clip.fps.denominator:.3f}"
        else:
            # FIXME: VFR support here
            total_duration = "0:00:00.000"
            fps_str = "0"

        info = OutputInfo(
            total_duration=total_duration,
            total_frames=voutput.clip.num_frames,
            width=voutput.clip.width,
            height=voutput.clip.height,
            format_name=voutput.clip.format.name if voutput.clip.format else "NONE",
            fps=fps_str,
        )

        self.statusOutputChanged.emit(info)

    def _on_reload_failed(self) -> None:
        self.load_content(self.content)

    def _on_timeline_clicked(self, frame: Frame, time: Time) -> None:
        logger.debug("Timeline clicked: frame=%d, time=%s", frame, time)
        self.request_frame(frame)

    def _on_frame_changed(self, frame: Frame, old_frame: Frame) -> None:
        logger.debug("Frame changed: frame=%d", frame)
        self.request_frame(frame)

    def _on_time_changed(self, time: QTime, old_time: QTime) -> None:
        logger.debug("Time changed: time=%s", time)

        # FIXME: Probably doesn't work with VFR here
        frame = cround(
            Time.from_qtime(time).total_seconds()
            * self.tbar.timeline.fps.numerator
            / self.tbar.timeline.fps.denominator
        )

        logger.debug("Time changed: frame=%d", frame)

        self.request_frame(frame)

    @run_in_background(name="PlaybackNextFrame")
    def _playback_next_frame(self) -> None:
        if not self.tbar.is_playing:
            return

        if not self.tab_manager.current_voutput:
            logger.error("No current video output during playback")
            self._toggle_playback()
            return

        total_frames = self.tab_manager.current_voutput.clip.num_frames

        if self._playback.buffer and self._playback.buffer._bundles:
            try:
                result = self._playback.buffer.get_next_frame()
            except Exception as e:
                logger.error(
                    "An error occured during the rendering of the frame %d with the message: (%s): %s",
                    self.current_frame + 1,
                    e.__class__.__name__,
                    e,
                )
                self.clear_failed_load()
                return

            if result:
                frame_n, frame, plugin_frames = result

                if self._playback.stop_at_frame is not None and frame_n >= self._playback.stop_at_frame:
                    self._toggle_playback()
                    return

                self._track_fps()

                self.current_frame = frame_n
                self.tab_manager.current_view.last_frame = frame_n

                try:
                    with self.env.use(), frame:
                        image = self._packer.frame_to_qimage(frame)

                    self.tab_manager.update_current_view(image)
                    self.update_timeline_cursor(frame_n)

                    self.api._on_current_frame_changed(frame_n, plugin_frames)
                finally:
                    for frame_to_close in plugin_frames.values():
                        frame_to_close.close()

                self._schedule_or_continue(frame_n + 1)
                return

        # Fallback to sync request
        next_frame = self.current_frame + 1

        if next_frame >= total_frames:
            self._toggle_playback()
            return

        self._schedule_or_continue(next_frame, sync=False)

    def _track_fps(self) -> None:
        now = perf_counter_ns()

        self._playback.fps_history.append(now)

        if (total_elapsed := self._playback.fps_history[-1] - self._playback.fps_history[0]) > 0:
            avg_fps = (len(self._playback.fps_history) - 1) * 1_000_000_000 / total_elapsed

            if now - self._playback.last_fps_update_ns >= FPS_UPDATE_INTERVAL_NS:
                self.statusLoadingStarted.emit(f"Playing @ {avg_fps:.3f} fps")
                self._playback.last_fps_update_ns = now

    def _schedule_or_continue(self, next_frame: int, sync: bool = False) -> None:
        def sync_on_complete(_: object) -> None:
            self._playback_next_frame()

        if self.tbar.playback_container.settings.uncapped:
            if sync:
                self.request_frame(next_frame, sync_on_complete)
            else:
                self._playback_next_frame()
            return

        self._playback.next_frame_time_ns += self._playback.frame_interval_ns

        if (delay_ns := self._playback.next_frame_time_ns - perf_counter_ns()) > MIN_FRAME_DELAY_NS:
            self._playback.pending_frame = next_frame
            self.loop.from_thread(
                lambda: self.tbar.playback_timer.start(cround(delay_ns / 1_000_000)) if self.tbar.is_playing else None
            )
        elif sync:
            self.request_frame(next_frame, sync_on_complete)
        else:
            self._playback_next_frame()

    def _on_playback_timer(self) -> None:
        self.tbar.playback_timer.stop()
        if self.tbar.is_playing:
            self._playback_next_frame()

    def _toggle_playback(self) -> None:
        self.tbar.is_playing = not self.tbar.is_playing

        if self.tbar.is_playing:
            self._start_playback()
        else:
            self._stop_playback()

    @run_in_background(name="StartPlayback")
    def _start_playback(self, loop_range: range | None = None, stop_at: int | None = None) -> None:
        logger.debug("Starting playback")

        self.tbar.set_playback_controls_enabled(False)
        self.tbar.timeline.is_events_blocked = True

        # Wait for any pending buffer cleanup before creating new buffer
        # This prevents accumulation of buffers when user spams play/pause
        self._playback.wait_for_cleanup(
            timeout=1.0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer...")
        )

        self.tbar.is_playing = True
        self._playback.reset()
        self._playback.stop_at_frame = stop_at

        if not (voutput := self.tab_manager.current_voutput):
            return

        # Calculate target frame interval for FPS limiting
        fps = voutput.clip.fps
        total_frames = voutput.clip.num_frames

        if self.tbar.playback_container.settings.uncapped:
            self._playback.frame_interval_ns = 0
        elif fps.denominator > 0:
            self._playback.frame_interval_ns = cround(
                1_000_000_000 * fps.denominator / (fps.numerator * self.tbar.playback_container.settings.speed)
            )
        else:
            self._playback.frame_interval_ns = 0

        # Initialize target time for first frame
        self._playback.next_frame_time_ns = perf_counter_ns()
        self._playback.buffer = FrameBuffer(video_output=voutput, env=self.env)
        self.api._on_playback_started()
        # Register visible plugin nodes for pre-fetching
        self.api._register_plugin_nodes_to_buffer()

        self._playback.buffer.allocate(self.current_frame, total_frames, loop_range=loop_range)

        logger.debug(
            "Target frame interval: %d ns (fps=%s), buffer_size=%d",
            self._playback.frame_interval_ns,
            fps,
            self._playback.buffer._size,
        )

        self.statusLoadingStarted.emit("Playing...")

        # Start the render chain - each frame will chain to the next
        self._playback_next_frame()

    @run_in_loop(return_future=False)
    def _stop_playback(self) -> None:
        logger.debug("Stopping playback")
        self.tbar.set_playback_controls_enabled(True)
        self.tbar.timeline.is_events_blocked = False
        self.tbar.is_playing = False
        self.tbar.playback_timer.stop()

        self._playback.reset()

        self.tbar.playback_container.play_pause_btn.setChecked(False)
        self.api._on_playback_stopped()
        self.statusLoadingFinished.emit("Paused")

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
            self.workspacePluginsLoaded.emit()

    def _setup_docks(self) -> None:
        for plugin_type in PluginManager.tooldocks:
            dock = QDockWidget(plugin_type.display_name, self.dock_container)
            dock.setObjectName(plugin_type.identifier)
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
            dock.setVisible(False)

            plugin_obj = plugin_type(dock, self.api)
            dock.setWidget(plugin_obj)
            dock.visibilityChanged.connect(lambda visible: self._init_visible_plugins() if visible else None)

            self.plugins.append(plugin_obj)
            self.docks.append(dock)

            self.dock_container.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

            if len(self.docks) > 1:
                self.dock_container.tabifyDockWidget(self.docks[0], dock)

        # Docks are hidden by default, so toggle button starts unchecked
        self.dock_toggle_btn.setChecked(False)

    def _setup_panels(self) -> None:
        for i, plugin_type in enumerate(PluginManager.toolpanels):
            plugin_obj = plugin_type(self.plugin_splitter.plugin_tabs, self.api)

            self.plugins.append(plugin_obj)
            self.plugin_splitter.add_plugin(plugin_obj, plugin_type.display_name)
            self.plugin_splitter.plugin_tabs.setTabVisible(
                i, self.global_settings.view_tools.panels.get(plugin_type.identifier, True)
            )


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

    def loader(self) -> None:
        module = ModuleType("__vsview__")
        module.__dict__.update(self.vsargs)

        match self.content_type:
            case "script":
                self.script = load_script(self._script_content, self.env, module=module, **self._script_kwargs)
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

    def clear_environment(self) -> None:
        super().clear_environment()

        if hasattr(self, "script"):
            return clear_environment(self.script)

        return None
