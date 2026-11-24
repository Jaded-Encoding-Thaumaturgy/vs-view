from __future__ import annotations

import os
import weakref
from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import contextmanager
from functools import partial
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, final, override
from weakref import WeakKeyDictionary

import vapoursynth as vs
from pydantic import BaseModel
from PySide6.QtCore import QObject, QRect, Signal, Slot
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QDockWidget, QSplitter, QTabWidget, QWidget
from shiboken6 import Shiboken
from vsengine import UnifiedFuture
from vsengine.loops import Cancelled

from vsview.app.settings import SettingsManager
from vsview.app.utils import QObjectSet, has_overriding_method
from vsview.types import Frame, Time
from vsview.vsenv.loop import run_in_loop

from ._interface import _PluginLike, _PluginSettingsStore, _SettingsProxy, _ViewportCursorReset
from .contracts import AudioOutputProxy, VideoOutputProxy
from .exceptions import BlockerRefusedError, NoCurrentVideoOutputError, NoCurrentViewError

if TYPE_CHECKING:
    from vsview.app.outputs import FrameBuffer
    from vsview.app.workspace import LoaderWorkspace
    from vsview.app.workspace.callbacks import CallbackHandle

    from .api import (
        GraphicsViewProxy,
        PlaybackProxy,
        PluginAPI,
        PluginGraphicsView,
        TimelineProxy,
        WidgetPluginBase,
        _PluginBase,
    )


logger = getLogger(__name__)


@final
class PluginWorkspaceController(QObject):
    statusMessage = Signal(str)
    notificationRequested = Signal(object)  # Notification
    globalSettingsChanged = Signal()
    localSettingsChanged = Signal(str)
    aboutToSaveGlobal = Signal()
    aboutToSaveLocal = Signal(str)

    def __init__(self, workspace: LoaderWorkspace[Any]) -> None:
        super().__init__(workspace)
        self.__workspace = workspace
        self.__settings_store = _PluginSettingsStore(self._get_file_path)
        self.__cursor_reset = _ViewportCursorReset(self)
        self.__cursor_reset_lock = Lock()
        self.__cursor_reset_connected = False
        self.__missed_while_hidden: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.__missed_refresh: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()

        SettingsManager.signals.connect_global_weak(self._on_global_settings_changed)
        SettingsManager.signals.connect_local_weak(self._on_local_settings_changed)
        SettingsManager.signals.connect_about_global_weak(self._on_about_to_save_global)
        SettingsManager.signals.connect_about_local_weak(self._on_about_to_save_local)

        from .api import PluginAPI

        self.api = PluginAPI(self)
        register_controller(self.api, self)

        self.context_menu_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.mouse_move_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.mouse_pressed_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.mouse_released_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.rect_selection_changed_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.rect_selection_finished_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.key_press_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()
        self.key_release_subscribers: QObjectSet[WidgetPluginBase[Any, Any]] = QObjectSet()

        self.globalSettingsChanged.connect(self.api.globalSettingsChanged)
        self.localSettingsChanged.connect(self.api.localSettingsChanged)
        self.aboutToSaveGlobal.connect(self.api.aboutToSaveGlobal)
        self.aboutToSaveLocal.connect(self.api.aboutToSaveLocal)
        self.api.statusMessage.connect(self.statusMessage)
        self.api.notificationRequested.connect(self.notificationRequested)

    @override
    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if cls is not PluginWorkspaceController:
            raise TypeError(f"Subclassing {PluginWorkspaceController.__name__} is not allowed")

    @property
    def file_path(self) -> Path | None:
        return self._get_file_path()

    @property
    def current_frame(self) -> Frame:
        return Frame(self.__workspace.playback.state.current_frame)

    @property
    def current_time(self) -> Time:
        if voutput := self.__workspace.outputs_manager.current_voutput:
            return voutput.frame_to_time(self.current_frame)
        raise NoCurrentVideoOutputError("No video output is currently available in the active workspace.")

    @property
    def current_video_index(self) -> int:
        return self.__workspace.outputs_manager.current_video_index

    @property
    def busy(self) -> bool:
        return self.__workspace.lifecycle.busy

    @property
    def generation(self) -> int:
        return self.__workspace.lifecycle.generation

    @property
    def voutputs(self) -> list[VideoOutputProxy]:
        return [
            VideoOutputProxy(
                v.vs_index,
                v.vs_name,
                v.vs_output,
                v.props,
                v.framedurs,
                v.cum_durations,
                v.midpoints,
                v.kwargs,
                v.info,
                v.packer,
            )
            for v in self.__workspace.outputs_manager.voutputs
        ]

    @property
    def current_voutput(self) -> VideoOutputProxy:
        if v := self.__workspace.outputs_manager.current_voutput:
            return VideoOutputProxy(
                v.vs_index,
                v.vs_name,
                v.vs_output,
                v.props,
                v.framedurs,
                v.cum_durations,
                v.midpoints,
                v.kwargs,
                v.info,
                v.packer,
            )
        raise NoCurrentVideoOutputError("No video output is currently available in the active workspace.")

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        return [
            AudioOutputProxy(aoutput.vs_index, aoutput.vs_name, aoutput.vs_output, aoutput.kwargs)
            for aoutput in self.__workspace.outputs_manager.aoutputs
        ]

    @property
    def current_aoutput(self) -> AudioOutputProxy | None:
        if aoutput := self.__workspace.outputs_manager.current_aoutput:
            return AudioOutputProxy(aoutput.vs_index, aoutput.vs_name, aoutput.vs_output, aoutput.kwargs)
        return None

    @property
    def is_playing(self) -> bool:
        return self.__workspace.playback.state.is_playing

    @property
    def current_view_proxy(self) -> GraphicsViewProxy:
        from .api import GraphicsViewProxy

        tab_manager = self.__workspace.tab_manager

        try:
            current_view = tab_manager.current_view
        except TypeError:
            raise NoCurrentViewError("No current view is currently available in the active workspace")

        with self.__cursor_reset_lock:
            if not self.__cursor_reset_connected:
                tab_manager.tabChanged.connect(self.__cursor_reset.reset)
                self.__cursor_reset_connected = True

        return GraphicsViewProxy(self.__cursor_reset, current_view)

    @property
    def timeline_proxy(self) -> TimelineProxy:
        from .api import TimelineProxy

        return TimelineProxy(self.__workspace.tbar.timeline)

    @property
    def playback_proxy(self) -> PlaybackProxy:
        from .api import PlaybackProxy

        return PlaybackProxy(self.__workspace.playback)

    def create_blocker(self, api: PluginAPI, caller: WidgetPluginBase[Any, Any] | None = None) -> WorkspaceBlocker:
        lifecycle = self.__workspace.lifecycle
        target = caller or api

        return WorkspaceBlocker(partial(lifecycle.try_hold, target), partial(lifecycle.release_hold, target))

    def get_local_storage(self, plugin: _PluginBase[Any, Any]) -> Path | None:
        if not self.file_path:
            return None

        settings_path = SettingsManager.local_settings_path(self.file_path)
        local_storage = settings_path.with_suffix("").with_stem(settings_path.stem.upper()) / plugin.identifier
        local_storage.mkdir(parents=True, exist_ok=True)
        return local_storage

    def register_on_destroy(self, cb: Callable[[], Any]) -> CallbackHandle:
        return self.__workspace.register_on_release(cb)

    @contextmanager
    def vs_context(self) -> Generator[None]:
        with self.__workspace.env.use():
            yield

    # Lifecycle & Plugin Orchestration
    def init_plugin(self, plugin: WidgetPluginBase[Any, Any], refresh: bool = False) -> None:
        if not self._is_truly_visible(plugin):
            if refresh:
                self.__missed_refresh.add(plugin)
            return

        refresh = refresh or plugin in self.__missed_refresh
        self.__missed_while_hidden.discard(plugin)
        self.__missed_refresh.discard(plugin)

        with self._isolated("on_current_voutput_changed", plugin.identifier):
            plugin.on_current_voutput_changed(
                self.current_voutput,
                self.__workspace.outputs_manager.current_video_index,
            )

            for view in self._visible_views(plugin):
                with self._isolated("init_view", view):
                    self.init_view(view, plugin, refresh)

    def register_plugin_nodes_to_buffer(self, buffer: FrameBuffer) -> None:
        for plugin in self.__workspace.plugins:
            if not self._is_truly_visible(plugin):
                continue

            for view in self._visible_views(plugin):
                if view.current_tab in view.outputs:
                    buffer.register_plugin_node(f"{plugin.identifier}:{id(view)}", view.outputs[view.current_tab])

    def update_subscribers(self) -> None:
        from .api import WidgetPluginBase

        subscriber_method_map = (
            ("on_view_context_menu", self.context_menu_subscribers),
            ("on_view_mouse_moved", self.mouse_move_subscribers),
            ("on_view_mouse_pressed", self.mouse_pressed_subscribers),
            ("on_view_mouse_released", self.mouse_released_subscribers),
            ("on_view_rect_selection_changed", self.rect_selection_changed_subscribers),
            ("on_view_rect_selection_finished", self.rect_selection_finished_subscribers),
            ("on_view_key_press", self.key_press_subscribers),
            ("on_view_key_release", self.key_release_subscribers),
        )

        for plugin in self.__workspace.plugins:
            for method_name, subscriber_set in subscriber_method_map:
                if has_overriding_method(WidgetPluginBase, plugin, method_name):
                    subscriber_set.add(plugin)

    def on_workspace_loaded(self) -> None:
        for plugin in self.__workspace.plugins:
            with self._isolated("on_workspace_loaded", plugin.identifier):
                plugin.on_workspace_loaded()

    def on_workspace_destroy(self) -> None:
        for plugin in self.__workspace.plugins:
            with self._isolated("on_workspace_destroy", plugin.identifier):
                plugin.on_workspace_destroy()

    def on_workspace_shown(self) -> None:
        for plugin in list(self.__missed_while_hidden):
            self.init_plugin(plugin)

    def init_view(
        self,
        view: PluginGraphicsView,
        plugin: WidgetPluginBase[Any, Any] | None = None,
        refresh: bool = False,
    ) -> None:
        if plugin is None and (plugin := self._find_plugin_for_widget(view)) is None:
            return

        if not self._is_truly_visible(plugin):
            if refresh:
                self.__missed_refresh.add(plugin)
            return

        tab_index = self.__workspace.outputs_manager.current_video_index
        current_frame = self.__workspace.playback.state.current_frame

        image_changed = view.current_tab != tab_index or view.last_frame != current_frame
        view.current_tab = tab_index
        view.last_frame = current_frame

        logger.debug("Initializing view: %s, tab=%d (changed=%s), refresh=%s", view, tab_index, image_changed, refresh)

        if refresh:
            view.outputs.clear()

        if tab_index not in view.outputs:
            with self.__workspace.env.use():
                node = view.get_node(self.current_voutput.vs_output.clip)
                view.outputs[tab_index] = self.current_voutput.packer_sdr.pack_clip(node)
                logger.debug("Created output node for tab %d", tab_index)

        with self.__workspace.env.use():
            view.on_current_voutput_changed(self.current_voutput, tab_index)

        if view.pixmap_item.pixmap().isNull() or image_changed or refresh:
            target_tab = tab_index
            target_frame = current_frame

            def on_frame_ready(f: Future[vs.VideoFrame]) -> None:
                if not Shiboken.isValid(view):
                    if not f.exception():
                        f.result().close()
                    return

                if exc := f.exception():
                    logger.error("Failed to render initial frame %d for view %s", target_frame, view, exc_info=exc)
                    return

                with (frame := f.result()):
                    if view.current_tab == target_tab and view.last_frame == target_frame:
                        logger.debug("Rendering initial frame %d for view", target_frame)
                        with self._isolated("on_current_frame_changed", view):
                            view.on_current_frame_changed(target_frame, frame)
                        view.update_scene_rect()
                        view.set_autofit(view.autofit)

            with self.__workspace.env.use():
                fut = view.outputs[tab_index].get_frame_async(current_frame)
            UnifiedFuture.from_future(fut).add_loop_callback(on_frame_ready)
        else:
            view.update_scene_rect()
            view.set_autofit(view.autofit)

    def on_current_voutput_changed(self, refresh: bool = False) -> None:
        for plugin in self.__workspace.plugins:
            self.init_plugin(plugin, refresh)

    def on_current_frame_changed(self, n: int, plugin_frames: dict[str, vs.VideoFrame] | None = None) -> None:
        for plugin in self.__workspace.plugins:
            if not self._is_truly_visible(plugin):
                continue

            with self._isolated("on_current_frame_changed", plugin.identifier):
                plugin.on_current_frame_changed(n)

            for view in self._visible_views(plugin):
                if view.current_tab == -1 or view.current_tab not in view.outputs:
                    continue

                view_key = f"{plugin.identifier}:{id(view)}"
                with self._isolated("on_current_frame_changed", view_key):
                    if plugin_frames and view_key in plugin_frames:
                        view.on_current_frame_changed(n, plugin_frames[view_key])
                    else:
                        logger.debug("Synchronous get_frame for %s", view_key)
                        with self.__workspace.env.use(), view.outputs[view.current_tab].get_frame(n) as frame:
                            view.on_current_frame_changed(n, frame)

    @run_in_loop(return_future=False)
    def on_frame_displayed(self, n: int) -> None:
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                with self._isolated("on_frame_displayed", plugin.identifier):
                    plugin.on_frame_displayed(n)

    @run_in_loop(return_future=False)
    def on_playback_started(self) -> None:
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                with self._isolated("on_playback_started", plugin.identifier):
                    plugin.on_playback_started()

    @run_in_loop(return_future=False)
    def on_playback_stopped(self) -> None:
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                with self._isolated("on_playback_stopped", plugin.identifier):
                    plugin.on_playback_stopped()

    # Settings Access & Updates
    def get_settings_proxy(self, plugin: _PluginLike, scope: str) -> _SettingsProxy[BaseModel] | None:
        return self.__settings_store.get_proxy(plugin, scope)

    def update_settings(self, plugin: _PluginLike, scope: str, **updates: Any) -> None:
        self.__settings_store.update(plugin, scope, **updates)

    def populate_local_settings(self) -> None:
        from .manager import PluginManager

        PluginManager.populate_default_settings(self.__settings_store.local_settings())

    # Qt View Event Forwarding
    @Slot(QContextMenuEvent)
    def on_view_context_menu(self, event: QContextMenuEvent) -> None:
        for plugin in self.context_menu_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_context_menu", plugin.identifier):
                    plugin.on_view_context_menu(event)

    @Slot(QMouseEvent)
    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        for plugin in self.mouse_move_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_mouse_moved", plugin.identifier):
                    plugin.on_view_mouse_moved(event)

    @Slot(QMouseEvent)
    def on_view_mouse_pressed(self, event: QMouseEvent) -> None:
        for plugin in self.mouse_pressed_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_mouse_pressed", plugin.identifier):
                    plugin.on_view_mouse_pressed(event)

    @Slot(QMouseEvent)
    def on_view_mouse_released(self, event: QMouseEvent) -> None:
        for plugin in self.mouse_released_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_mouse_released", plugin.identifier):
                    plugin.on_view_mouse_released(event)

    @Slot(QRect)
    def on_view_rect_selection_changed(self, rect: QRect) -> None:
        for plugin in self.rect_selection_changed_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_rect_selection_changed", plugin.identifier):
                    plugin.on_view_rect_selection_changed(rect)

    @Slot(QRect)
    def on_view_rect_selection_finished(self, rect: QRect) -> None:
        for plugin in self.rect_selection_finished_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_rect_selection_finished", plugin.identifier):
                    plugin.on_view_rect_selection_finished(rect)

    @Slot(QKeyEvent)
    def on_view_key_press(self, event: QKeyEvent) -> None:
        for plugin in self.key_press_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_key_press", plugin.identifier):
                    plugin.on_view_key_press(event)

    @Slot(QKeyEvent)
    def on_view_key_release(self, event: QKeyEvent) -> None:
        for plugin in self.key_release_subscribers:
            if self._is_truly_visible(plugin):
                with self._isolated("on_view_key_release", plugin.identifier):
                    plugin.on_view_key_release(event)

    # Internal helperss
    @run_in_loop(return_future=False)
    def _is_truly_visible(self, plugin: WidgetPluginBase[Any, Any]) -> bool:
        if not self.__workspace.isVisible():
            # Showing the workspace page again sends no visibility signal to panel plugins,
            # so on_workspace_shown replays what was skipped here.
            self.__missed_while_hidden.add(plugin)
            return False

        if not plugin.isVisible():
            return False

        widget: QObject | None = plugin

        while widget:
            parent = widget.parent()

            if isinstance(parent, QTabWidget) and parent.currentWidget() is not plugin:
                return False

            if isinstance(parent, QDockWidget) and parent.visibleRegion().isEmpty():
                return False

            if (
                isinstance(parent, QSplitter)
                and isinstance(widget, QWidget)
                and (idx := parent.indexOf(widget)) >= 0
                and parent.sizes()[idx] == 0
            ):
                return False

            widget = parent

        return True

    @run_in_loop(return_future=False)
    def _visible_views(self, plugin: WidgetPluginBase[Any, Any]) -> list[PluginGraphicsView]:
        from .api import PluginGraphicsView

        return [v for v in plugin.findChildren(PluginGraphicsView) if v.isVisible()]

    @run_in_loop(return_future=False)
    def _find_plugin_for_widget(self, widget: QWidget) -> WidgetPluginBase[Any, Any] | None:
        from .api import WidgetPluginBase

        current: QObject | None = widget.parent()

        while current is not None:
            if isinstance(current, WidgetPluginBase):
                return current
            current = current.parent()
        logger.warning("Could not find plugin for widget %r", widget)
        return None

    def _get_file_path(self) -> Path | None:
        from vsview.app.workspace import BaseGenericFileWorkspace

        if not isinstance(self.__workspace, BaseGenericFileWorkspace):
            return None

        # A workspace may return any PathLike, such as a Path subclass with its own behaviour
        return None if (path := self.__workspace.current_file_path) is None else Path(os.fspath(path))

    @staticmethod
    @contextmanager
    def _isolated(hook: str, target: object) -> Generator[None]:
        try:
            yield
        except Cancelled as e:
            logger.debug("%s was cancelled for %r: %s", hook, target, e)
        except Exception:
            logger.exception("%s failed for %r", hook, target)

    def _on_global_settings_changed(self) -> None:
        self.globalSettingsChanged.emit()

    def _on_local_settings_changed(self, path: str) -> None:
        self.localSettingsChanged.emit(path)

    def _on_about_to_save_global(self) -> None:
        self.aboutToSaveGlobal.emit()

    def _on_about_to_save_local(self, path: str) -> None:
        self.aboutToSaveLocal.emit(path)


class WorkspaceBlocker:
    def __init__(self, try_hold: Callable[[], bool], release_hold: Callable[[], None]) -> None:
        self._lock = Lock()
        self.locked = False
        self._try_hold = try_hold
        self._release_hold = release_hold

    def __enter__(self) -> None:
        if not self.acquire(block=True):
            raise BlockerRefusedError("The workspace is replacing its environment or closing")

    def __exit__(self, *_: object) -> None:
        self.release()

    def acquire(self, block: bool = True, timeout: float | None = None) -> bool:
        t = -1.0 if timeout is None else timeout

        if not self._lock.acquire(blocking=block, timeout=t):
            return False

        if not self.locked:
            if not self._try_hold():
                self._lock.release()
                return False
            self.locked = True

        return True

    def release(self) -> None:
        if self.locked:
            self.locked = False
            self._release_hold()
            self._lock.release()


def _controller_factory() -> tuple[
    Callable[[PluginAPI], PluginWorkspaceController],
    Callable[[PluginAPI, PluginWorkspaceController], None],
    Callable[[PluginAPI], None],
]:
    # In-process private mapping: PluginAPI -> PluginWorkspaceController
    controllers: WeakKeyDictionary[PluginAPI, weakref.ReferenceType[PluginWorkspaceController]] = WeakKeyDictionary()

    def get_controller(api: PluginAPI) -> PluginWorkspaceController:
        if (ref_ctrl := controllers.get(api)) and (ctrl := ref_ctrl()):
            return ctrl
        raise RuntimeError("Workspace controller is no longer alive or API is unassociated.")

    def register_controller(api: PluginAPI, controller: PluginWorkspaceController) -> None:
        controllers[api] = weakref.ref(controller)

    def unregister_controller(api: PluginAPI) -> None:
        controllers.pop(api, None)

    return get_controller, register_controller, unregister_controller


get_controller, register_controller, _ = _controller_factory()
