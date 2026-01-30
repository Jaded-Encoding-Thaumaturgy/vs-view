from __future__ import annotations

from abc import abstractmethod
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from functools import partial
from logging import getLogger
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, ClassVar, Literal, assert_never

from jetpytools import clamp
from PySide6.QtCore import QSignalBlocker, Qt, Signal
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

from ...vsenv import gc_collect, run_in_background, run_in_loop, unset_environment
from ..outputs import AudioOutput, OutputsManager, VideoOutput
from ..plugins.api import PluginAPI, WidgetPluginBase
from ..plugins.manager import PluginManager
from ..settings import ActionID, ShortcutManager
from ..views import OutputInfo, PluginSplitter
from ..views.components import CustomLoadingPage, DockButton
from ..views.timeline import Frame, Time, TimelineControlBar
from .base import BaseWorkspace
from .playback import PlaybackManager
from .tab_manager import TabManager

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
        self.loaded_layout.addWidget(self.tbar)
        self.stack.addWidget(self.loaded_page)

        # API & plugins
        self.api = PluginAPI(self)
        self.cbs_on_destroy = list[Callable[[], Any]]()
        self.plugins = list[WidgetPluginBase]()
        self.docks = list[QDockWidget]()
        self.plugins_loaded = False

        self.outputs_manager = OutputsManager(self.api)

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
        self.playback.frameRendered.connect(
            lambda image: self.tab_manager.update_current_view(
                image,
                skip_adjustments=not self.playback.can_reload,
            )
        )
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

    def deleteLater(self) -> None:
        logger.debug(
            "%s(%r) deleteLater called, cleaning up resources",
            self.__class__.__name__,
            lambda: content.name if isinstance(content := getattr(self, "content", None), Path) else content,
        )

        self.playback.stop()
        self.playback.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))
        self.loop.wait_for_threads()

        return super().deleteLater()

    def clear_environment(self) -> None:
        with self.env.use():
            for cb in self.cbs_on_destroy:
                self.loop.from_thread(cb)

        self.outputs_manager.clear()

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

            try:
                voutputs, aoutputs = self._get_outputs()
            except Exception:
                self.clear_failed_load()
                return

        tabs = self.tab_manager.create_tabs(voutputs, self.playback.state.current_frame)

        with QSignalBlocker(self.tab_manager):
            self.tab_manager.swap_tabs(tabs, self.outputs_manager.current_video_index)

        self.tbar.playback_container.set_audio_outputs(aoutputs, self.outputs_manager.current_audio_index)

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
            self.playback.can_reload = True

        # Handle potiential error on frame rendering
        try:
            self._on_tab_changed(self.outputs_manager.current_video_index, cb_render=on_complete)
        except Exception:
            logger.error("Failed to load content: %r", self.content)
            self.clear_failed_load()
            return

        logger.info("Content loaded successfully: %r", self.content)
        self.statusLoadingFinished.emit("Completed")

    @run_in_background(name="ReloadContent")
    def reload_content(self) -> None:
        if not self.playback.can_reload:
            logger.warning("Workspace is busy, cannot reload content")
            return

        logger.debug("Reloading content: %r", self.content)

        self.playback.stop()
        self.playback.can_reload = False
        self.statusLoadingStarted.emit("Reloading Content...")

        with self.tbar.disabled(), self.tab_manager.clear_voutputs_on_fail():
            self.loop.from_thread(self.content_area.setDisabled, True)
            self.tab_manager.disable_switch = True
            self.playback.wait_for_cleanup(0.25, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

            # 1. Capture and Preserve State
            saved_state = self.tab_manager.current_view.state

            @run_in_loop(return_future=False)
            def preserve_ui() -> None:
                self.tab_manager.current_view.pixmap_item.setPixmap(saved_state.pixmap)
                for view in self.tab_manager.tabs.views():
                    if view is not self.tab_manager.current_view:
                        view.clear_scene()

            preserve_ui()

            # 2. Reset Environment
            self.clear_environment()
            gc_collect()

            # 3. Load New Content
            with loader_lock:
                self.env.switch()
                try:
                    voutputs, aoutputs = self._get_outputs()
                except Exception:
                    self.clear_failed_load()
                    return

            # 4. Reconstruct UI
            tabs = self.tab_manager.create_tabs(voutputs, self.playback.state.current_frame, enabled=False)

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

            self.tbar.playback_container.set_audio_outputs(aoutputs, self.outputs_manager.current_audio_index)

            @run_in_loop(return_future=False)
            def on_complete(f: Future[None]) -> None:
                if f.exception():
                    return
                saved_state.restore_view_state(self.tab_manager.current_view)
                self.content_area.setEnabled(True)
                self.tab_manager.tabs.setEnabled(True)
                self.playback.can_reload = True
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
        self.playback.stop()
        self.playback.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

        with QSignalBlocker(self.tab_manager.tabs):
            self.tab_manager.tabs.clear()

        self.clear_environment()

        self.statusLoadingErrored.emit("Error while loading content")
        self.set_error_page()
        gc_collect()

    @run_in_loop
    def init_timeline(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            logger.debug("No voutput available")
            return

        fps = voutput.clip.fps
        total_frames = voutput.clip.num_frames

        self.tbar.timeline.set_data(total_frames, fps)

        # Use configured FPS history size, or auto-calculate from FPS when set to 0
        if (fps_history_size := self.global_settings.playback.fps_history_size) <= 0:
            fps_history_size = round(fps.numerator / fps.denominator)

        self.playback.state.fps_history = deque(maxlen=clamp(fps_history_size, 1, total_frames))

        with QSignalBlocker(self.tbar.playback_container.frame_edit):
            self.tbar.playback_container.frame_edit.setMaximum(Frame(total_frames - 1))

        with QSignalBlocker(self.tbar.playback_container.time_edit):
            self.tbar.playback_container.time_edit.setMaximumTime(self.tbar.timeline.total_time.to_qtime())

        self.tbar.playback_container.fps = fps

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

    def _get_outputs(self) -> tuple[list[VideoOutput], list[AudioOutput]]:
        self.loader()

        voutputs = self.outputs_manager.create_voutputs(
            self.content,
            self.video_outputs,
            self.get_output_metadata(),
        )

        if not voutputs:
            raise RuntimeError

        aoutputs = self.outputs_manager.create_aoutputs(
            self.content,
            self.audio_outputs,
            self.get_output_metadata(),
            delay_s=self.tbar.playback_container.audio_delay,
        )

        return voutputs, aoutputs

    def _on_dock_toggle(self, checked: bool) -> None:
        for dock in self.docks:
            if self.global_settings.view_tools.docks.get(dock.objectName(), True):
                dock.setVisible(checked)

    def _init_visible_plugins(self) -> None:
        if not self.outputs_manager.current_voutput:
            return  # No content loaded yet

        with self.env.use():
            for plugin in self.plugins:
                self.api._init_plugin(plugin)

    def _on_tab_changed(
        self, index: int, seamless: bool = False, cb_render: Callable[[Future[None]], None] | None = None
    ) -> None:
        if not self.outputs_manager.current_voutput:
            logger.debug("Invalid tab index %d, ignoring", index)
            return

        self.playback.stop()

        logger.debug("Switched to video output: clip=%r", self.outputs_manager.current_voutput.clip)

        self.init_timeline()
        target_frame = self._calculate_target_frame()
        self.update_timeline_cursor(target_frame)
        self.outputs_manager.current_video_index = index
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
            self.playback.request_frame(target_frame, on_complete)
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

        assert self.outputs_manager.current_voutput

        src_fps = self.outputs_manager.voutputs[self.tab_manager.tabs.previous_tab_index].clip.fps
        tgt_fps = self.outputs_manager.current_voutput.clip.fps

        current_time = self.outputs_manager.current_voutput.frame_to_time(self.playback.state.current_frame, src_fps)
        target_frame = self.outputs_manager.current_voutput.time_to_frame(current_time, tgt_fps)

        target_frame = clamp(target_frame, 0, self.outputs_manager.current_voutput.clip.num_frames - 1)

        logger.debug(
            "Sync playhead enabled, targeting frame %d (from time %.3fs)",
            target_frame,
            current_time.total_seconds(),
        )
        return target_frame

    def _emit_output_info(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
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
