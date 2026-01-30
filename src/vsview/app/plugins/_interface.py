from __future__ import annotations

from itertools import zip_longest
from logging import getLogger
from pathlib import Path
from types import get_original_bases
from typing import TYPE_CHECKING, Any, get_args, get_origin
from weakref import WeakKeyDictionary

import vapoursynth as vs
from pydantic import BaseModel
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDockWidget, QSplitter, QTabWidget, QWidget

from vsview.app.settings import SettingsManager
from vsview.app.utils import ObjectType
from vsview.vsenv.loop import run_in_loop

if TYPE_CHECKING:
    from vsview.app.workspace.loader import LoaderWorkspace

    from .api import PluginGraphicsView, VideoOutputProxy, WidgetPluginBase, _PluginBase

logger = getLogger(__name__)


class _PluginSettingsStore:
    def __init__(self, workspace: LoaderWorkspace[Any]) -> None:
        self._workspace = workspace
        self._global_cache: WeakKeyDictionary[_PluginBase[Any, Any], BaseModel] = WeakKeyDictionary()
        self._local_cache: WeakKeyDictionary[_PluginBase[Any, Any], BaseModel] = WeakKeyDictionary()

    @property
    def file_path(self) -> Path | None:
        from vsview.app.workspace.file import GenericFileWorkspace

        return self._workspace.content if isinstance(self._workspace, GenericFileWorkspace) else None

    def get(self, plugin: _PluginBase[Any, Any], scope: str) -> BaseModel | None:
        cache = self._global_cache if scope == "global" else self._local_cache

        if plugin in cache:
            return cache[plugin]

        # Get the settings model for this scope
        model: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model")

        if model is None:
            return None

        # Fetch raw data from storage + validate into model
        settings = model.model_validate(self._get_raw_settings(plugin.identifier, scope))

        # Resolve local settings with global fallbacks
        from .api import LocalSettingsModel

        if (
            scope == "local"
            and isinstance(settings, LocalSettingsModel)
            and (global_settings := self.get(plugin, "global")) is not None
        ):
            settings = settings.resolve(global_settings)

        cache[plugin] = settings
        return settings

    def update(self, plugin: _PluginBase[Any, Any], scope: str, **updates: Any) -> None:
        # For local settings, we need to update the raw (unresolved) settings,
        # not the resolved version with global fallbacks merged in.
        if (settings := self._get_unresolved_settings(plugin, scope)) is None:
            return

        # Apply updates to the settings object
        for key, value in updates.items():
            setattr(settings, key, value)

        # Persist to storage
        self._set_raw_settings(plugin.identifier, scope, settings)

        # Invalidate cache so next access re-validates
        cache = self._global_cache if scope == "global" else self._local_cache
        cache.pop(plugin, None)

    def invalidate(self, scope: str) -> None:
        getattr(self, f"_{scope}_cache").clear()

    def _get_raw_settings(self, plugin_id: str, scope: str) -> dict[str, Any]:
        if scope == "global":
            container = SettingsManager.global_settings
        elif self.file_path is not None:
            container = SettingsManager.get_local_settings(self.file_path)
        else:
            return {}

        raw = container.plugins.get(plugin_id, {})

        return raw if isinstance(raw, dict) else raw.model_dump()

    def _set_raw_settings(self, plugin_id: str, scope: str, settings: BaseModel) -> None:
        if scope == "global":
            SettingsManager.global_settings.plugins[plugin_id] = settings
        elif self.file_path is not None:
            SettingsManager.get_local_settings(self.file_path).plugins[plugin_id] = settings

    def _get_unresolved_settings(self, plugin: _PluginBase[Any, Any], scope: str) -> BaseModel | None:
        model: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model")

        if model is None:
            return None

        return model.model_validate(self._get_raw_settings(plugin.identifier, scope))


class _PluginAPI(QObject):
    statusMessage = Signal(str)
    globalSettingsChanged = Signal()
    localSettingsChanged = Signal(str)

    def __init__(self, workspace: LoaderWorkspace[Any]) -> None:
        super().__init__()
        self.__workspace = workspace
        self.__settings_store: _PluginSettingsStore | None = None

        SettingsManager.signals.globalChanged.connect(self._on_global_settings_changed)
        SettingsManager.signals.localChanged.connect(self._on_local_settings_changed)

    @property
    def current_voutput(self) -> VideoOutputProxy:
        """Return the VideoOutput for the currently selected tab."""
        from .api import VideoOutputProxy

        if voutput := self.__workspace.outputs_manager.current_voutput:
            return VideoOutputProxy(voutput.vs_index, voutput.vs_name, voutput.vs_output, voutput.props)

        # This shouldn't happen
        raise NotImplementedError

    # PRIVATE API
    @property
    def _settings_store(self) -> _PluginSettingsStore:
        if self.__settings_store is None:
            self.__settings_store = _PluginSettingsStore(self.__workspace)
        return self.__settings_store

    def _is_truly_visible(self, plugin: WidgetPluginBase[Any, Any]) -> bool:
        # Check if this plugin is truly visible to the user.

        # This accounts for:
        # - Widgets in a QTabWidget that are not the current tab
        # - Widgets in a QDockWidget that is tabified and not visible
        # - Widgets in a QSplitter panel that is collapsed (size=0)
        if not plugin.isVisible():
            return False

        widget: QObject | None = plugin

        while widget:
            parent = widget.parent()

            # Plugin is not the current tab (compare against self, not intermediate widget)
            if isinstance(parent, QTabWidget) and parent.currentWidget() is not plugin:
                return False

            # Dock widget is tabified and not visible
            if isinstance(parent, QDockWidget) and parent.visibleRegion().isEmpty():
                return False

            # Check if our panel in the splitter is collapsed
            if (
                isinstance(parent, QSplitter)
                and isinstance(widget, QWidget)
                and (idx := parent.indexOf(widget)) >= 0
                and parent.sizes()[idx] == 0
            ):
                return False

            widget = parent

        return True

    def _register_plugin_nodes_to_buffer(self) -> None:
        # Register visible plugin nodes with the buffer for pre-fetching during playback.
        from .api import PluginGraphicsView

        for plugin in self.__workspace.plugins:
            if not self._is_truly_visible(plugin):
                continue

            for view in plugin.findChildren(PluginGraphicsView):
                if view.current_tab in view.outputs and self.__workspace.playback.state.buffer:
                    self.__workspace.playback.state.buffer.register_plugin_node(
                        plugin.identifier, view.outputs[view.current_tab]
                    )

    def _on_current_voutput_changed(self) -> None:
        # Notify all visible plugin views of output change.
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                self._init_plugin(plugin)

    def _init_plugin(self, plugin: WidgetPluginBase[Any, Any]) -> None:
        # Initialize plugin for the current output and render initial frame if needed.
        from .api import PluginGraphicsView

        if not self._is_truly_visible(plugin):
            return

        try:
            plugin.on_current_voutput_changed(
                self.current_voutput,
                self.__workspace.outputs_manager.current_video_index,
            )
        except Exception:
            logger.exception("on_current_voutput_changed: Failed to initialize plugin %r", plugin)
            return

        try:
            plugin.on_current_frame_changed(self.__workspace.playback.state.current_frame)
        except Exception:
            logger.exception("on_current_frame_changed: Failed to initialize plugin %r", plugin)
            return

        for view in plugin.findChildren(PluginGraphicsView):
            try:
                self._init_view(view, plugin)
            except Exception:
                logger.exception("Failed to initialize view %r", view)

    def _find_plugin_for_widget(self, widget: QWidget) -> WidgetPluginBase[Any, Any] | None:
        from .api import WidgetPluginBase

        current: QObject | None = widget.parent()

        while current is not None:
            if isinstance(current, WidgetPluginBase):
                return current
            current = current.parent()
        logger.warning("Could not find plugin for widget %r", widget)
        return None

    def _init_view(
        self, view: PluginGraphicsView, plugin: WidgetPluginBase[Any, Any] | None = None, refresh: bool = False
    ) -> None:
        if plugin is None and (plugin := self._find_plugin_for_widget(view)) is None:
            return

        if not self._is_truly_visible(plugin):
            return

        tab_index = self.__workspace.outputs_manager.current_video_index
        current_frame = self.__workspace.playback.state.current_frame

        # Detect if we are actually changing tabs or forcing a refresh
        output_changed = view.current_tab != tab_index
        view.current_tab = tab_index

        logger.debug("Initializing view: %s, tab=%d (changed=%s), refresh=%s", view, tab_index, output_changed, refresh)

        if refresh:
            view.outputs.clear()

        if tab_index not in view.outputs:
            with self.__workspace.env.use():
                node = view.get_node(self.current_voutput.vs_output.clip)
                packed = self.__workspace.outputs_manager.packer.pack_clip(node)
                view.outputs[tab_index] = packed
                logger.debug("Created output node for tab %d", tab_index)

        with self.__workspace.env.use():
            view.on_current_voutput_changed(self.current_voutput, tab_index)

        if view.pixmap_item.pixmap().isNull() or output_changed or refresh:
            with self.__workspace.env.use(), view.outputs[tab_index].get_frame(current_frame) as frame:
                logger.debug("Rendering initial frame %d for view", current_frame)
                view.on_current_frame_changed(current_frame, frame)

        view.setSceneRect(view.pixmap_item.boundingRect())
        view.set_autofit(view.autofit)

    def _on_current_frame_changed(self, n: int, plugin_frames: dict[str, vs.VideoFrame] | None = None) -> None:
        # Notify plugins of frame change.
        # If plugin_frames is provided, uses pre-fetched frames.
        # Otherwise, fetches frames synchronously for each plugin view.
        from .api import PluginGraphicsView

        for plugin in self.__workspace.plugins:
            if not self._is_truly_visible(plugin):
                continue

            plugin.on_current_frame_changed(n)

            for view in plugin.findChildren(PluginGraphicsView):
                if view.current_tab == -1 or view.current_tab not in view.outputs:
                    continue

                # Get pre-fetched frame or fall back to sync request
                if plugin_frames and plugin.identifier in plugin_frames:
                    view.on_current_frame_changed(n, plugin_frames[plugin.identifier])
                else:
                    with self.__workspace.env.use(), view.outputs[view.current_tab].get_frame(n) as frame:
                        view.on_current_frame_changed(n, frame)

    def _get_cached_settings(self, plugin: _PluginBase[Any, Any], scope: str) -> Any:
        return self._settings_store.get(plugin, scope)

    def _update_settings(self, plugin: _PluginBase[Any, Any], scope: str, **updates: Any) -> None:
        self._settings_store.update(plugin, scope, **updates)

    @run_in_loop(return_future=False)
    def _on_playback_started(self) -> None:
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                plugin.on_playback_started()

    def _on_playback_stopped(self) -> None:
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                plugin.on_playback_stopped()

    def _on_global_settings_changed(self) -> None:
        self._settings_store.invalidate("global")
        self._settings_store.invalidate("local")
        self.globalSettingsChanged.emit()

    def _on_local_settings_changed(self, path: str) -> None:
        self._settings_store.invalidate("local")
        self.localSettingsChanged.emit(path)


class _PluginBaseMeta(ObjectType):
    global_settings_model: type[BaseModel] | None
    local_settings_model: type[BaseModel] | None

    def __new__[MetaSelf: _PluginBaseMeta](  # noqa: PYI019
        mcls: type[MetaSelf],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        **kwargs: Any,
    ) -> MetaSelf:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)

        # Skip processing for base classes that set __plugin_base__ = True
        if namespace.get("__plugin_base__", False):
            return cls

        # WidgetPluginBase and NodeProcessor are now defined so it's safe to import them
        from .api import NodeProcessor, WidgetPluginBase

        for base in get_original_bases(cls):
            if not (origin := get_origin(base)):
                continue

            args = get_args(base)

            if issubclass(origin, WidgetPluginBase):
                scope = ["global", "local"]
            elif issubclass(origin, NodeProcessor):
                scope = [None, "global", "local"]
            else:
                continue

            for n, arg in zip_longest(scope, args, fillvalue=None):
                if arg is None or not isinstance(arg, type):
                    setattr(cls, f"{n}_settings_model", None)
                    continue

                origin = get_origin(arg)
                is_basemodel = (origin is None and issubclass(arg, BaseModel)) or (
                    origin is not None and issubclass(origin, BaseModel)
                )
                setattr(cls, f"{n}_settings_model", arg if is_basemodel else None)
            break
        else:
            cls.global_settings_model, cls.local_settings_model = None, None

        return cls
