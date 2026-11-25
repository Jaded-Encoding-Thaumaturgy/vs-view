from __future__ import annotations

import gc
import re
from abc import abstractmethod
from collections import deque
from collections.abc import Callable, Generator, Sequence
from concurrent.futures import Future, wait
from contextlib import ExitStack, contextmanager, suppress
from functools import partial
from logging import getLogger
from pathlib import Path
from threading import RLock
from types import ModuleType
from typing import Any, ClassVar, Literal, assert_never, override

from jetpytools import clamp, fallback
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal, Slot
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
from shiboken6 import Shiboken
from vsengine import keep_environment
from vsengine.loops import Cancelled
from vsengine.policy import ManagedEnvironment
from vsengine.vpy import ExecutionError, Script, load_code, load_script

from ...api._helpers import output_metadata
from ...api.info import Context
from ...env import getenv_bool
from ...types import Frame
from ...vsenv import run_in_background, run_in_loop, unset_environment
from ...vsenv.loop import TaskInfo
from ..cache.job import JobState
from ..outputs import AudioOutput, OutputsManager, VideoOutput
from ..plugins._controller import PluginWorkspaceController
from ..plugins.api import WidgetPluginBase
from ..settings import ActionID, SettingsManager, ShortcutManager
from ..settings.models import CacheRange
from ..utils import ProcessMemorySnapshot, has_overriding_method, measure_memory
from ..views import PluginDock, PluginSplitter
from ..views.components import BlockableWidget, CustomLoadingPage, DockButton
from ..views.tab import TabViewWidget
from ..views.timeline import CacheRenderAction, TimelineControlBar
from ..views.video import GraphicsView, ViewState
from .base import BaseWorkspace
from .callbacks import CallbackHandle, CallbackRegistry
from .lifecycle import LoadResult, Phase, StaleGenerationError
from .playback import PlaybackManager, show_warning
from .render_cache import RenderCacheController
from .tab_manager import PlayHeadToolButton, TabManager
from .utils import State, evict_packages, find_local_packages

loader_lock = RLock()
logger = getLogger(__name__)


class LoaderWorkspace[T](BaseWorkspace):
    """A workspace that supports loading content."""

    content: T
    """The content being loaded."""

    task_timeout_ms: ClassVar[int] = 10_000
    """How long a teardown or a reload waits for the workspace's tasks to end."""

    # Status bar signals
    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message
    statusLoadingErrored = Signal(str)  # error message
    statusOutputChanged = Signal(object)  # OutputInfo dataclass

    # Notification signal
    notificationRequested = Signal(object)  # Notification

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.reload_count = 0

        self.stack = QStackedWidget(self)
        self.current_layout.addWidget(self.stack)

        # API & plugins
        self.plugin_controller = PluginWorkspaceController(self)
        self._release_callbacks = CallbackRegistry()
        self._unload_callbacks = CallbackRegistry()
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
        self.tab_manager = TabManager(self.plugin_splitter, self.plugin_controller)
        self.tab_manager.switch_allowed = lambda: self.lifecycle.phase is Phase.LOADED
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
            self.plugin_controller,
            self.outputs_manager,
            self.tab_manager,
            self.tbar,
            parent=self,
        )

        # Connect PlaybackManager signals to UI handlers
        self.playback.frameRendered.connect(self.tab_manager.update_current_view)
        self.playback.timelineCursorChanged.connect(self.update_timeline_cursor)
        self.playback.seekRefused.connect(self.restore_timeline_cursor)
        self.playback.loadFailed.connect(self._on_render_failed)

        # Audio control signals
        self.playback.audioOutputChanged.connect(
            lambda index: setattr(self.outputs_manager, "current_audio_index", index)
        )

        self.render_cache = RenderCacheController(self.loop, lambda: self.env, self.outputs_manager, parent=self)
        self.render_cache.changed.connect(self._on_render_cache_changed)
        self.render_cache.notificationRequested.connect(self.notificationRequested)

        # Connected after `_on_tab_changed`, which sets the current output the render cache reads.
        self.tab_manager.tabChanged.connect(self.render_cache.on_current_output_changed)
        self.playback.playbackStateChanged.connect(self.render_cache.set_playing)

        self.tbar.timeline.contextMenuRequested.connect(self._on_timeline_context_menu)
        self.tbar.timeline.cacheStartRequested.connect(self.render_cache.set_start)
        self.tbar.timeline.cacheEndRequested.connect(self.render_cache.set_end)
        self.tbar.timeline.cacheRenderToggled.connect(self.render_cache.toggle_render)
        self.tbar.timeline.cacheClearRequested.connect(self.render_cache.clear)
        self.tbar.timeline.cacheConcurrencyChanged.connect(self.render_cache.set_concurrency_override)
        self.tbar.playback_container.zoneToCacheRequested.connect(self._on_zone_to_cache_requested)

        self._set_shorcuts()
        self._register_shortcuts()

        SettingsManager.signals.connect_global_weak(self._set_shorcuts)

    def _set_shorcuts(self) -> None:
        sm = ShortcutManager()
        self.tbar.timeline.cache_start_action.setShortcut(sm.get_key(ActionID.CACHE_SET_START))
        self.tbar.timeline.cache_end_action.setShortcut(sm.get_key(ActionID.CACHE_SET_END))
        self.tbar.timeline.cache_render_action.setShortcut(sm.get_key(ActionID.CACHE_TOGGLE_RENDER))
        self.tbar.timeline.cache_clear_action.setShortcut(sm.get_key(ActionID.CACHE_CLEAR))

    def _register_shortcuts(self) -> None:
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
            lambda: self.playback.seek_to_frame_coalesced(Frame(0)),
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.SEEK_LAST_FRAME,
            lambda: self.playback.seek_to_frame_coalesced(Frame(0x7FFFFFFF)),
            self.loaded_page,
        )
        for action, forward, starts_only in (
            (ActionID.SEEK_PREVIOUS_NOTCH, False, False),
            (ActionID.SEEK_NEXT_NOTCH, True, False),
            (ActionID.SEEK_PREVIOUS_NOTCH_START, False, True),
            (ActionID.SEEK_NEXT_NOTCH_START, True, True),
        ):
            sm.register_shortcut(
                action,
                partial(self.seek_to_notch, forward=forward, starts_only=starts_only),
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
        sm.register_shortcut(ActionID.ZONE_PLAY, self.tbar.playback_container.zone_btn.click, self.loaded_page)
        sm.register_shortcut(
            ActionID.ZONE_SET_START,
            lambda: self.tbar.playback_container.set_zone_start(self.playback.state.current_frame),
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.ZONE_SET_END,
            lambda: self.tbar.playback_container.set_zone_end(self.playback.state.current_frame),
            self.loaded_page,
        )

        # Render cache
        sm.register_shortcut(
            ActionID.CACHE_SET_START,
            lambda: self.render_cache.set_start(self.playback.state.current_frame),
            self.loaded_page,
        )
        sm.register_shortcut(
            ActionID.CACHE_SET_END,
            lambda: self.render_cache.set_end(self.playback.state.current_frame),
            self.loaded_page,
        )
        sm.register_shortcut(ActionID.CACHE_TOGGLE_RENDER, self.render_cache.toggle_render, self.loaded_page)
        sm.register_shortcut(ActionID.CACHE_CLEAR, self.render_cache.clear, self.loaded_page)

        sm.register_shortcut(ActionID.RELOAD, self.reload_content, self.loaded_page)
        sm.register_shortcut(ActionID.RELOAD, self.reload_btn.click, self.error_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_FRAME, self._copy_current_frame_to_clipboard, self.loaded_page)
        sm.register_shortcut(ActionID.COPY_CURRENT_TIME, self._copy_current_time_to_clipboard, self.loaded_page)

        for i, action in enumerate(act for act in ActionID if act.name.startswith("SWITCH_TAB_")):
            sm.register_shortcut(action, partial(self.tab_manager.switch_tab, i), self)

        sm.register_shortcut(ActionID.SWITCH_PREVIOUS_TAB, lambda: self.tab_manager.switch_tab(delta=-1), self)
        sm.register_shortcut(ActionID.SWITCH_NEXT_TAB, lambda: self.tab_manager.switch_tab(delta=1), self)

    @override
    def _finish_tasks(self) -> Sequence[Future[Any]]:
        self.playback.stop()
        self.playback.invalidate_renders()
        self.playback.state.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))
        render_cache_release = self.render_cache.release()
        self.plugin_controller.on_workspace_destroy()
        self.loop.wait_for_threads(self.task_timeout_ms, owner=self)

        return [*(task.future for task in self.loop.pending_tasks(owner=self)), render_cache_release]

    @override
    def _release_content(self) -> None:
        if not wait([self.render_cache.release()], timeout=self.task_timeout_ms / 1000).done:
            logger.warning("Releasing the environment while render cache frames are still in flight")

        if (env := self._env) is None or env.disposed:
            # Their run's environment is gone, and VapourSynth calls made outside an environment are undefined.
            self._unload_callbacks.take_all()
        else:
            with env.use():
                done, _ = wait(self.loop.from_thread(keep_environment(cb)) for cb in self._release_callbacks.snapshot())
            for f in done:
                if (exc := f.exception()) is not None:
                    logger.error(exc, exc_info=exc)

        if content := getattr(self, "content", None):
            output_metadata.pop(str(content), None)

        self.outputs_manager.clear()

        super()._release_content()

    @override
    def _before_disposal(self, env: ManagedEnvironment) -> None:
        with env.use():
            self._unload_callbacks.run_callbacks(self, take_all=True, reverse=True)

    def register_on_release(self, cb: Callable[[], Any]) -> CallbackHandle:
        """
        Register a callback run each time this workspace releases its environment,
        before each reload, after a failed load and when it closes,
        on the GUI thread inside the environment, in FIFO order.

        Toolpanels and tooldocks use this through the controller and API.
        """
        return self._release_callbacks.add(cb)

    def register_on_unload[**P](self, cb: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> CallbackHandle:
        """
        Register a callback run once, the next time this workspace releases its environment,
        on the releasing thread inside the environment, in LIFO order.
        """
        return self._unload_callbacks.add(cb, *args, **kwargs)

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

        self.render_cache.set_ranges({})
        self.render_cache.set_concurrency_override(None)

        try:
            PluginManager.wait_for_loaded()
        except Exception as e:  # noqa: BLE001
            logger.warning("Plugin loading failed (%s); loading the content with the plugins that loaded", e)

    def on_views_created(self, views: Sequence[GraphicsView]) -> None:
        """Lifecycle hook called when views have been created for video outputs."""

    def on_before_reload(self) -> None:
        """Lifecycle hook called before content reload begins."""

    def on_content_loaded(self) -> None:
        """Lifecycle hook called when initial content loading and rendering completes."""
        self.tab_manager.set_global_autofit(self.tab_manager.autofit_btn.isChecked())
        self.on_content_ready(is_reload=False)

    def on_content_reloaded(self) -> None:
        """Lifecycle hook called when content reload and rendering completes."""
        self.on_content_ready(is_reload=True)

    def on_content_ready(self, *, is_reload: bool = False) -> None:
        """Lifecycle hook called when content is ready (either initial load or reload)."""

    def on_shown(self) -> None:
        """Replay the plugin updates that were skipped while this workspace was not the current page."""
        if self.lifecycle.phase is Phase.LOADED:
            with self.env.use():
                self.plugin_controller.on_workspace_shown()

    @run_in_background(name="LoadContent")
    def load_content(
        self,
        content: T,
        /,
        frame: int | None = None,
        time: float | None = None,
        tab_index: int | None = None,
    ) -> LoadResult:
        """
        Load `content`. A workspace that holds loaded content releases it first, as a reload does.

        Raises:
            Cancelled: When the workspace keeps its loaded content
                when the load had not released it yet, and fails otherwise.
        """
        logger.debug("load_content called: path=%r, frame=%r, tab_index=%r", content, frame, tab_index)

        if (phase := self.lifecycle.try_begin_load()) is None:
            self._warn_refused(f"load {content!r}")
            return LoadResult.STOPPED

        return self._run_generation(
            content,
            reload=False,
            replacing=phase is Phase.REPLACING,
            frame=frame,
            time=time,
            tab_index=tab_index,
        )

    @Slot()
    @run_in_background(name="ReloadContent")
    def reload_content(self) -> LoadResult:
        """
        Reload the loaded content, and keep the current tab, frame and view.

        Raises:
            Cancelled: When the workspace keeps its loaded content
                when the reload had not released it yet, and fails otherwise.
        """
        if not self.lifecycle.try_begin_reload():
            self._warn_refused("reload content")
            return LoadResult.STOPPED

        logger.debug("Reloading content: %r", self.content)
        return self._run_generation(self.content, reload=True, replacing=True)

    def _run_generation(
        self,
        content: T,
        *,
        reload: bool,
        replacing: bool,
        frame: int | None = None,
        time: float | None = None,
        tab_index: int | None = None,
    ) -> LoadResult:
        ui = ExitStack()
        if reload and getenv_bool("VSVIEW_DEBUG"):
            ui.enter_context(measure_memory())
        saved_view: tuple[ViewState, int, bool] | None = None

        try:
            if replacing:
                # Disable UI interaction so the user can't fuck this up
                self.playback.stop()
                self.playback.invalidate_renders()
                if reload:
                    self.on_before_reload()
                    self.statusLoadingStarted.emit("Reloading Content...")
                self.loop.from_thread(self.content_area.setDisabled, True).result()
                if reload:
                    ui.callback(self._destroy_freeze_screenshot, self._create_freeze_screenshot())
                self.tbar.disable()

                # Wait for tasks
                self.playback.state.wait_for_cleanup(
                    0.25,
                    stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."),
                )
                self.loop.wait_for_threads(self.task_timeout_ms, owner=self)
                self.loop.next_cycle().result()

                # If there was still running tasks we refuse the (re)load
                if still_running := self.loop.pending_tasks(owner=self):
                    ui.close()
                    self._refuse_replacement(still_running, reload=reload)
                    return LoadResult.STOPPED

                # Set the status again since it could have been changed by the stall cb above
                if reload:
                    self.statusLoadingStarted.emit("Reloading Content...")

                if not self.lifecycle.commit_replacement():
                    raise StaleGenerationError(f"The loaded content of {type(self).__name__} cannot be released")

                if reload:
                    saved_view = self._capture_reload_ui_state()
                    self._clear_reload_views()

                # We waited for running tasks so this method can't raise an error.
                self.release_environment()
                self.loop.from_thread(gc.collect).result()

                if getenv_bool("VSVIEW_DEBUG"):
                    logger.debug("Memory after environment cleanup: %s", ProcessMemorySnapshot())

            generation = self.lifecycle.next_generation()

            if reload:
                self.reload_count += 1

                with loader_lock:
                    if local_packages := find_local_packages():
                        evict_packages(local_packages)
                        logger.info("Local packages reloaded: %r", sorted(local_packages))
            else:
                # First load
                self.set_loading_page()
                self.statusLoadingStarted.emit("Loading...")
                self.content = content
                unset_environment()
                self.init_load(frame, time, tab_index)

            outputs = self._get_outputs()
            generation.check()

            # The workspace can't be in a state where there is no video output.
            if not outputs:
                ui.close()
                self.clear_failed_load()
                return LoadResult.FAILED

            voutputs, aoutputs = outputs

            if saved_view is not None:
                tabs = self.tab_manager.create_tabs(voutputs, enabled=False)
            else:
                self.outputs_manager.current_video_index = clamp(
                    self.outputs_manager.current_video_index, 0, len(voutputs) - 1
                )
                tabs = self.tab_manager.create_tabs(voutputs)

            # PERF: Check if a subclass implemented `on_views_created`
            if has_overriding_method(LoaderWorkspace, self, "on_views_created"):
                self.on_views_created(self.loop.from_thread(lambda: list(tabs.views())).result())

            if saved_view is not None:
                current_tab_i = self._restore_reload_tabs(tabs, voutputs, *saved_view)
            else:
                with QSignalBlocker(self.tab_manager):
                    self.tab_manager.swap_tabs(tabs, self.outputs_manager.current_video_index)
                current_tab_i = self.loop.from_thread(lambda: self.tab_manager.tabs.currentIndex()).result()

            self.tbar.playback_container.set_audio_outputs(aoutputs, self.outputs_manager.current_audio_index)

            if not self.lifecycle.begin_rendering():
                raise StaleGenerationError(f"The new outputs of {type(self).__name__} cannot render")

            # Plugins are built once the outputs exist, so they can use the new environment.
            self.load_plugins()
            generation.check()
            self.plugin_controller.on_workspace_loaded()

            @run_in_loop(return_future=False)
            def on_complete(f: Future[int]) -> None:
                ui.close()

                if not generation.is_current():
                    return

                if f.exception():
                    logger.error("Failed to load content: %r", self.content)
                    self.clear_failed_load()
                    return

                if not self.lifecycle.finish(generation):
                    logger.warning(
                        "Workspace is %s, the load of %r cannot end",
                        self.lifecycle.phase.name.lower(),
                        self.content,
                    )
                    return

                self.content_area.setEnabled(True)
                self.tab_manager.tabs.setEnabled(True)
                self.tbar.enable()
                self.render_cache.on_outputs_ready(is_reload=reload)

                if reload:
                    self.content_area.setFocus()
                    logger.info("Content reloaded successfully: %r", self.content)
                    self.on_content_reloaded()
                else:
                    logger.info("Content loaded successfully: %r", self.content)
                    self.on_content_loaded()
                    self.statusLoadingFinished.emit("Completed")

            # The workspace's own render must not be stopped by a busy plugin, or the load would never end.
            self._on_tab_changed(
                current_tab_i,
                seamless=reload,
                cb_render=on_complete,
                refresh_plugins=reload,
                check_busy=False,
            )

            return LoadResult.RENDERING
        except StaleGenerationError:
            logger.debug("The load of %r stopped because the workspace is closing", content)
            ui.close()
            return LoadResult.STOPPED
        except Cancelled:
            logger.debug("The load of %r was cancelled", content)

            with suppress(Cancelled):
                ui.close()
                if self._keep_loaded_content():
                    self.statusLoadingErrored.emit("Reload cancelled" if reload else "Load cancelled")
                else:
                    self.clear_failed_load()
            raise
        except Exception:
            logger.error("Failed to load content: %r", content)
            ui.close()
            self.clear_failed_load()
            raise

    def _warn_refused(self, action: str) -> None:
        if self.lifecycle.phase is Phase.LOADED and self.lifecycle.busy:
            logger.warning("At least one plugin is busy, cannot %s", action)
        else:
            logger.warning("Workspace is %s, cannot %s", self.lifecycle.phase.name.lower(), action)

    @run_in_loop(return_future=False)
    def _keep_loaded_content(self) -> bool:
        """Return False when the loaded content was already released, and is no longer kept."""
        if not self.lifecycle.cancel_reload():
            return False

        self.content_area.setEnabled(True)
        self.tbar.enable()
        return True

    def _refuse_replacement(self, still_running: list[TaskInfo], *, reload: bool) -> None:
        self._keep_loaded_content()

        if reload:
            kind, outcome = "Reload", "The content was not reloaded"
        else:
            kind, outcome = "Load", "The new content was not loaded"

        names = ", ".join(sorted({task.name for task in still_running}))
        message = (
            f"{outcome}: {len(still_running)} task(s) of this workspace are still running "
            f"({names}). Try again when they have finished."
        )
        logger.warning(message)
        self.statusLoadingErrored.emit(f"{kind} refused")
        self.loop.from_thread(show_warning, self, message, title=f"{kind} Refused").result()

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

        self.tab_manager.set_global_autofit(autofit_enabled, under_reload=True)

        return resolved_index

    @run_in_loop(return_future=False)
    def _create_freeze_screenshot(self) -> QLabel:
        viewport = self.tab_manager.current_view.viewport()
        screenshot = QLabel(self)
        screenshot.setPixmap(viewport.grab())  # Grab just the viewport, no frame/scrollbars

        pos = viewport.mapTo(self, viewport.rect().topLeft())
        screenshot.setGeometry(pos.x(), pos.y(), viewport.width(), viewport.height())
        screenshot.raise_()
        screenshot.show()
        return screenshot

    @run_in_loop(return_future=False)
    def _destroy_freeze_screenshot(self, screenshot: QLabel) -> None:
        screenshot.setVisible(False)
        screenshot.deleteLater()

    @run_in_loop(return_future=False)
    def clear_failed_load(self, *, loaded_only: bool = False) -> bool:
        """
        Mark the workspace as failed, release its environment and show the error page.

        Args:
            loaded_only: Fail only loaded content. A frame of the loaded content that fails to render sets it,
                because a load or reload that runs reports its own failure.

        Returns:
            True when the workspace moved to the failed phase.
            False when it has already failed, is closing, or holds no loaded content while `loaded_only` is set.
        """
        if not self.lifecycle.fail(loaded_only=loaded_only):
            logger.debug("Workspace is %s, skipping clear_failed_load...", self.lifecycle.phase.name.lower())
            return False

        self.playback.stop()
        self.playback.invalidate_renders()
        self.playback.state.wait_for_cleanup(0, stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."))

        self.release_environment()

        with QSignalBlocker(self.tab_manager.tabs):
            self.tab_manager.tabs.clear()

        self.statusLoadingErrored.emit("Error while loading content")
        self.set_error_page()
        self.tbar.enable()
        gc.collect()

        return True

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

    @Slot(int)
    @run_in_loop
    def update_timeline_cursor(self, n: int) -> None:
        if self.playback.has_pending_frame:
            return

        self._set_cursor(n, force=False)

    @Slot(int)
    def restore_timeline_cursor(self, n: int) -> None:
        self._set_cursor(n, force=True)

    def _set_cursor(self, n: int, *, force: bool) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            return

        frame = Frame(n)

        if force:
            self.tbar.timeline.force_cursor(frame)
        else:
            self.tbar.timeline.cursor_x = frame

        with QSignalBlocker(self.tbar.playback_container.frame_edit):
            self.tbar.playback_container.frame_edit.setValue(frame)

        with QSignalBlocker(self.tbar.playback_container.time_edit):
            self.tbar.playback_container.time_edit.setTime(voutput.frame_to_time(frame).to_qtime())

    @run_in_loop(return_future=False)
    def set_loaded_page(self) -> None:
        self.stack.setCurrentWidget(self.loaded_page)
        self.content_area.setFocus()

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

    def calculate_target_frame(self, state: PlayHeadToolButton.State | None = None) -> int:
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
                return self.calculate_target_frame(resolved_state)
            case _:
                assert_never(s)

    def _get_outputs(self) -> tuple[list[VideoOutput], list[AudioOutput]] | None:
        try:
            with self.prepare_environment().use():
                self.loader()

                voutputs = self.outputs_manager.create_voutputs(
                    self.content,
                    self.video_outputs,
                    self.get_output_metadata(),
                    self.plugin_controller.api,
                    last_frame=self.playback.state.current_frame,
                )

                if not voutputs:
                    raise RuntimeError

                aoutputs = self.outputs_manager.create_aoutputs(
                    self.content,
                    self.audio_outputs,
                    self.get_output_metadata(),
                    self.plugin_controller.api,
                    delay_s=self.tbar.playback_container.audio_delay,
                )

                return voutputs, aoutputs
        except Exception:
            logger.debug("Full traceback:", exc_info=True)
            return None

    @Slot(int)
    @run_in_loop(return_future=False)
    def _on_tab_changed(
        self,
        index: int,
        seamless: bool = False,
        cb_render: Callable[[Future[int]], None] | None = None,
        refresh_plugins: bool = False,
        check_busy: bool = True,
    ) -> bool:
        self.playback.stop()
        self.playback.invalidate_renders()
        self.outputs_manager.current_video_index = index

        if not self.outputs_manager.current_voutput:
            logger.debug("Invalid tab index %d, ignoring", index)
            if cb_render:
                f = Future[int]()
                f.set_exception(RuntimeError(f"No video output at tab index {index}"))
                cb_render(f)
            return True

        logger.debug("Switching to video output: clip=%r", self.outputs_manager.current_voutput.vs_output.clip)

        target_frame = self.calculate_target_frame()
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
                    self.loaded_page.blocked = True
                    if (duration := self.global_settings.loaded_page_disable_duration) < 500:
                        # Disable the loaded page if the frame takes too much time to render
                        timer_disable = QTimer(self, singleShot=True, timerType=Qt.TimerType.PreciseTimer)
                        timer_disable.timeout.connect(lambda: self.loaded_page.setDisabled(True))
                        timer_disable.start(duration)
                    self.tab_manager.update_current_view(prev_pixmap)

            def on_complete(f: Future[int]) -> None:
                if not f.exception():
                    if timer_disable:
                        timer_disable.stop()
                        timer_disable.deleteLater()
                    self.loaded_page.setEnabled(True)
                    self.set_loaded_page()

                    if f.result() == 0:
                        with self.env.use():
                            self.plugin_controller.on_current_voutput_changed(refresh_plugins)

                if cb_render:
                    cb_render(f)

            logger.debug("Requesting frame %d", target_frame)
            return self.playback.request_frame(target_frame, on_complete, notify_plugins=False, check_busy=check_busy)

        with self.env.use():
            self.plugin_controller.on_current_voutput_changed(refresh_plugins)

        if cb_render:
            f = Future[int]()
            f.set_result(0)
            cb_render(f)

        return True

    def _emit_output_info(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            logger.warning("No current video output, ignoring")
            return

        self.statusOutputChanged.emit(voutput.info._replace(sar=self.tab_manager.current_view.display_sar))

    @Slot()
    def _on_reload_failed(self) -> None:
        self.load_content(self.content)

    @Slot()
    def _on_render_failed(self) -> None:
        self.clear_failed_load(loaded_only=True)

    @Slot()
    def _on_render_cache_changed(self) -> None:
        r = self.render_cache.current_range() or CacheRange()
        parts = self.render_cache.range_parts()

        self.tbar.timeline.set_cache_notches(r.start, r.end, " · ".join(parts), self.render_cache.cached_runs())

        title, *details = parts or ["Cache"]
        self.tbar.timeline.set_cache_summary(title, " · ".join(details))
        self.tbar.playback_container.zone_editor.set_cache_state(r.frames, self.render_cache.unavailable_reason())

    @Slot(object)
    def _on_timeline_context_menu(self, frame: Frame) -> None:
        r = self.render_cache.current_range()

        match self.render_cache.render_state():
            case JobState.PAUSED:
                render = CacheRenderAction.RESUME
            case JobState.RUNNING | JobState.WAITING:
                render = CacheRenderAction.PAUSE
            case _:
                render = CacheRenderAction.RENDER

        self.tbar.timeline.set_cache_actions(
            render,
            can_render=r is not None and r.frames is not None,
            can_clear=r is not None or bool(self.render_cache.cached_runs()),
            concurrency=self.render_cache.concurrency,
            concurrency_overridden=self.render_cache.concurrency_override is not None,
            unavailable_reason=self.render_cache.unavailable_reason(),
        )

    @Slot()
    def _on_zone_to_cache_requested(self) -> None:
        if not (voutput := self.outputs_manager.current_voutput):
            return

        self.render_cache.set_range(
            *self.tbar.playback_container.zone_editor.settings.bounds(
                self.playback.state.current_frame,
                voutput.vs_output.clip.num_frames,
            )
        )

    def seek_to_notch(self, *, forward: bool, starts_only: bool = False) -> None:
        """
        Seek to the nearest seekable timeline notch after or before the current frame.

        Args:
            forward: Seek to the next notch, or to the previous one when False.
            starts_only: Seek only to the start of each notch, and skip the ends of ranges.
        """
        if not (voutput := self.outputs_manager.current_voutput):
            return

        points = (
            p if isinstance(p, Frame) else voutput.time_to_frame(p)
            for p in self.tbar.timeline.seek_points(starts_only=starts_only)
        )
        self.playback.seek_to_neighbor(points, forward=forward)

    @Slot()
    def _copy_current_frame_to_clipboard(self) -> None:
        frame = self.tbar.playback_container.frame_edit.value()

        QApplication.clipboard().setText(str(frame))

        self.statusLoadingFinished.emit(f"Copied frame {frame}")
        logger.info("Copied frame %d to clipboard", frame)

    @Slot()
    def _copy_current_time_to_clipboard(self) -> None:
        timestamp = self.tbar.playback_container.time_edit.time().toString("H:mm:ss.zzz")

        QApplication.clipboard().setText(timestamp)

        self.statusLoadingFinished.emit(f"Copied time {timestamp}")
        logger.info("Copied time %s to clipboard", timestamp)

    @run_in_loop(return_future=False)
    def load_plugins(self) -> None:
        # Plugins built during the teardown would never receive on_workspace_destroy.
        if not self.plugins_loaded and not self.lifecycle.closing:
            tabs = self.plugin_splitter.plugin_tabs

            for dock in self.docks:
                self.dock_container.removeDockWidget(dock)
                dock.deleteLater()

            for plugin in self.plugins:
                if (index := tabs.indexOf(plugin)) != -1:
                    tabs.removeTab(index)
                    plugin.deleteLater()

            self.plugins.clear()
            self.docks.clear()

            with self.env.use():
                self._setup_docks()
                self._setup_panels()

            self.plugins_loaded = True
            self.plugin_controller.update_subscribers()

    def _setup_docks(self) -> None:
        from ..plugins.manager import PluginManager

        for plugin_type in PluginManager.tooldocks:
            dock = PluginDock(plugin_type.display_name, plugin_type.identifier, self.dock_container)

            try:
                plugin_obj = plugin_type(dock, self.plugin_controller.api)
            except Exception:
                logger.exception("Failed to create the tool dock %r, skipping it", plugin_type.identifier)
                dock.deleteLater()
                continue

            dock.setWidget(plugin_obj)
            dock.visibilityChanged.connect(self._on_dock_visibility_changed)

            self.plugins.append(plugin_obj)
            self.docks.append(dock)

            self.dock_container.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

            if len(self.docks) > 1:
                self.dock_container.tabifyDockWidget(self.docks[0], dock)

        # Docks are hidden by default, so toggle button starts unchecked
        self.dock_toggle_btn.setChecked(False)

    def _setup_panels(self) -> None:
        from ..plugins.manager import PluginManager

        tabs = self.plugin_splitter.plugin_tabs

        for plugin_type in PluginManager.toolpanels:
            children_before = {Shiboken.getCppPointer(child)[0] for child in tabs.children()}

            try:
                plugin_obj = plugin_type(tabs, self.plugin_controller.api)
            except Exception:
                logger.exception("Failed to create the tool panel %r, skipping it", plugin_type.identifier)
                for child in tabs.children():
                    if Shiboken.getCppPointer(child)[0] not in children_before and isinstance(child, QWidget):
                        child.deleteLater()
                continue

            self.plugins.append(plugin_obj)
            self.plugin_splitter.add_plugin(plugin_obj, plugin_type.display_name)
            tabs.setTabVisible(
                tabs.indexOf(plugin_obj),
                self.global_settings.view_tools.panels.get(plugin_type.identifier, True),
            )

    def _hide_plugin(self, plugin: WidgetPluginBase[Any, Any]) -> None:
        try:
            plugin.on_hide()
        except Exception:
            logger.exception("%s: Failed for plugin %r", plugin.on_hide, plugin.identifier)

    @Slot(bool)
    def _on_dock_toggle(self, checked: bool) -> None:
        for dock in self.docks:
            if self.global_settings.view_tools.docks.get(dock.objectName(), True):
                dock.setVisible(checked)

        with QSignalBlocker(self.dock_toggle_btn), QSignalBlocker(self.tab_manager.toggle_tooldock_btn):
            self.dock_toggle_btn.setChecked(checked)
            self.tab_manager.toggle_tooldock_btn.setChecked(checked)

    @Slot(bool)
    def _on_dock_visibility_changed(self, visible: bool) -> None:
        if not isinstance(dock := self.sender(), PluginDock):
            logger.warning("Calling '_on_dock_visibility_changed' but the sender isn't a PluginDock")
            return

        if not isinstance(w := dock.widget(), WidgetPluginBase):
            return

        if visible:
            if self.lifecycle.phase is Phase.LOADED:
                with self.env.use():
                    self.plugin_controller.init_plugin(w)

            dock.truly_visible = True
        elif dock.truly_visible:
            self._hide_plugin(w)
            dock.truly_visible = False

    @Slot(bool)
    def _sync_toolpanel_btn(self, visible: bool) -> None:
        with QSignalBlocker(self.tab_manager.toggle_toolpanel_btn):
            self.tab_manager.toggle_toolpanel_btn.setChecked(visible)

    @Slot(bool)
    def _on_splitter_visibility_changed(self, visible: bool = True) -> None:
        if not isinstance(w := self.plugin_splitter.plugin_tabs.currentWidget(), WidgetPluginBase):
            return

        if visible:
            if self.lifecycle.phase is Phase.LOADED:
                with self.env.use():
                    self.plugin_controller.init_plugin(w)
        else:
            self._hide_plugin(w)

    @Slot(int, int)
    def _on_splitter_tab_changed(self, new_index: int, old_index: int) -> None:
        if (
            isinstance(w := self.plugin_splitter.plugin_tabs.widget(new_index), WidgetPluginBase)
            and self.lifecycle.phase is Phase.LOADED
        ):
            with self.env.use():
                self.plugin_controller.init_plugin(w)

        if isinstance(w := self.plugin_splitter.plugin_tabs.widget(old_index), WidgetPluginBase):
            self._hide_plugin(w)


class VSEngineWorkspace[T](LoaderWorkspace[T]):
    """Base workspace for script execution."""

    content_type: ClassVar[Literal["script", "code"]]
    """The type of content to load."""

    script: Script[ManagedEnvironment]
    """The loaded script. Available only after loader() is called."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vsargs = dict[str, Any]()
        self.persistent_state = State()

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
    def on_teardown_finished(self) -> None:
        super().on_teardown_finished()
        self.persistent_state.clear()
        self.persistent_state = State()

    @override
    def loader(self) -> None:
        from ...app.error import show_error

        ctx = Context(
            reload_count=self.reload_count,
            is_reload=self.reload_count > 0,
            persistent_state=self.persistent_state,
            on_workspace_destroy=self.register_on_destroy,
            on_unload=self.register_on_unload,
        )

        module = ModuleType("__vsview__")
        module.__dict__.update(self.vsargs)
        module.__dict__.update(__vsview_context__=ctx)

        with loader_lock:
            match self.content_type:
                case "script":
                    chdir = Path(self._user_script_path).parent if self.global_settings.chdir else None
                    loader = load_script
                case "code":
                    chdir = None
                    loader = load_code
                case _:
                    assert_never(self.content_type)

            try:
                self.script = loader(self._script_content, self.env, module=module, chdir=chdir, **self._script_kwargs)
            except (SyntaxError, OSError, ValueError) as skill_issue:
                self.statusLoadingErrored.emit(re.sub(r"(?<=\w)([A-Z])", r" \1", skill_issue.__class__.__name__))
                show_error(skill_issue, self, self._user_script_path, phase="loading")
                raise RuntimeError("Script loading failed") from None
            except TypeError as e:
                raise RuntimeError("Script loading failed") from e

            logger.debug("Running Script...")

            try:
                self.script.result()
                logger.debug("%s execution completed successfully", self.content_type.title())
            except ExecutionError as e:
                execution_err = e
            else:
                execution_err = None

        if execution_err is not None:
            self.statusLoadingErrored.emit("Execution error")

            show_error(execution_err, self, self._user_script_path)
            # Clear traceback to release VS core references held in the exception chain
            execution_err.parent_error.__traceback__ = None
            execution_err.__traceback__ = None

            raise RuntimeError("Script execution failed") from None

    @override
    def _take_environment(self) -> ManagedEnvironment | Script[ManagedEnvironment] | None:
        if not hasattr(self, "script"):
            return super()._take_environment()

        script = self.script
        del self.script
        self._env = None
        return script
