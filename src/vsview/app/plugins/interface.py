"""
Plugin interface for VSView.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self
from weakref import WeakKeyDictionary

import vapoursynth as vs
from pydantic import BaseModel
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap, QShowEvent
from PySide6.QtWidgets import QDockWidget, QSplitter, QTabWidget, QWidget

from vsview.app.settings import SettingsManager
from vsview.app.views.video import BaseGraphicsView
from vsview.vsenv.loop import run_in_loop

if TYPE_CHECKING:
    from vsview.app.workspace.loader import LoaderWorkspace

_logger = getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VideoOutputProxy:
    """Read-only proxy for a video output."""

    vs_index: int
    """Index of the video output in the VapourSynth environment."""

    vs_name: str | None
    """Name of the video output, if any, when using `vsview.set_output()`."""

    vs_output: vs.VideoOutputTuple
    """The object created by `vapoursynth.get_outputs()`."""

    props: Mapping[int, Mapping[str, Any]]
    """
    Frame properties of the clip.
    The props of the current frame are guaranteed to be available.
    """


class LocalSettingsModel(BaseModel):
    """
    Base class for settings with optional local overrides.

    Fields set to `None` fall back to the corresponding global value.
    """

    def resolve(self, global_settings: BaseModel) -> Self:
        """
        Resolve global settings with local overrides applied.

        Args:
            global_settings: Source of default values.

        Returns:
            A new instance with all fields resolved.
        """
        base_values = global_settings.model_dump(include=set(self.__class__.model_fields))

        overrides = self.model_dump(exclude_none=True)

        return self.__class__(**base_values | overrides)


class PluginAPI(QObject):
    """API for plugins to interact with the workspace."""

    statusMessage = Signal(str)  # message
    """Signal to emit status messages."""

    globalSettingsChanged = Signal()
    """Signal to emit when global settings change."""

    localSettingsChanged = Signal(str)
    """Signal to emit when local settings change."""

    def __init__(self, workspace: LoaderWorkspace[Any]) -> None:
        super().__init__()
        self.__workspace = workspace
        self.__global_settings_cache = WeakKeyDictionary[PluginBase, Any]()
        self.__local_settings_cache = WeakKeyDictionary[PluginBase, Any]()

        SettingsManager.signals.globalChanged.connect(self._on_global_settings_changed)
        SettingsManager.signals.localChanged.connect(self._on_local_settings_changed)

    @property
    def file_path(self) -> Path | None:
        """Return the file path of the currently loaded file, or None if not a file."""
        from vsview.app.workspace.file import GenericFileWorkspace

        if isinstance(self.__workspace, GenericFileWorkspace):
            return self.__workspace.content
        return None

    @property
    def current_frame(self) -> int:
        """Return the current frame number."""
        return self.__workspace.current_frame

    @property
    def current_tab_index(self) -> int:
        """Return the index of the currently selected tab."""
        return self.__workspace.current_tab_index

    @property
    def voutputs(self) -> dict[int, VideoOutputProxy]:
        """Return a dictionary of VideoOutputProxy objects for all tabs."""
        return {
            k: VideoOutputProxy(voutput.vs_index, voutput.vs_name, voutput.vs_output, voutput.props)
            for k, voutput in self.__workspace.tab_manager.voutputs.items()
        }

    @property
    def current_voutput(self) -> VideoOutputProxy:
        """Return the VideoOutput for the currently selected tab."""
        if voutput := self.__workspace.tab_manager.current_voutput:
            return VideoOutputProxy(voutput.vs_index, voutput.vs_name, voutput.vs_output, voutput.props)

        # This shouldn't happen
        raise NotImplementedError

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        """
        Register a callback to be called before the workspace begins a reload or when the workspace is destroyed.
        This is generaly used to clean up VapourSynth resources.
        """
        self.__workspace.cbs_on_destroy.append(cb)

    def frame_to_pixmap(
        self,
        f: vs.VideoFrame,
        flags: Qt.ImageConversionFlag = Qt.ImageConversionFlag.NoFormatConversion,
    ) -> QPixmap:
        """
        Convert a VapourSynth frame to a QPixmap. Assume the frame is already packed in GRAY32 format.
        """
        return QPixmap.fromImage(self.__workspace._packer.frame_to_qimage(f), flags).copy()

    # PRIVATE API
    def _is_truly_visible(self, plugin: PluginBase) -> bool:
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
        for plugin in self.__workspace.plugins:
            if not self._is_truly_visible(plugin):
                return

            for view in plugin.findChildren(PluginGraphicsView):
                if view.current_tab in view.outputs and self.__workspace._playback.buffer:
                    self.__workspace._playback.buffer.register_plugin_node(
                        plugin.identifier, view.outputs[view.current_tab]
                    )

    def _on_current_voutput_changed(self) -> None:
        # Notify all visible plugin views of output change.
        for plugin in self.__workspace.plugins:
            if self._is_truly_visible(plugin):
                self._init_plugin(plugin)

    def _init_plugin(self, plugin: PluginBase) -> None:
        # Initialize plugin for the current output and render initial frame if needed.
        if not self._is_truly_visible(plugin):
            return

        tab_index = self.current_tab_index
        current_frame = self.current_frame

        try:
            plugin.on_current_voutput_changed(self.current_voutput, tab_index)
        except Exception:
            _logger.exception("on_current_voutput_changed: Failed to initialize plugin %r", plugin)
            return

        try:
            plugin.on_current_frame_changed(current_frame)
        except Exception:
            _logger.exception("on_current_frame_changed: Failed to initialize plugin %r", plugin)
            return

        for view in plugin.findChildren(PluginGraphicsView):
            try:
                self._init_view(view, plugin)
            except Exception:
                _logger.exception("Failed to initialize view %r", view)

    def _init_view(self, view: PluginGraphicsView, plugin: PluginBase, refresh: bool = False) -> None:
        if not self._is_truly_visible(plugin):
            return

        tab_index = self.current_tab_index
        current_frame = self.current_frame

        view.current_tab = tab_index
        _logger.debug("Found view: %s, current_tab=%d, outputs=%s", view, view.current_tab, list(view.outputs.keys()))

        if refresh:
            view.outputs.clear()

        if tab_index not in view.outputs:
            with self.__workspace.env.use():
                node = view.get_node(self.current_voutput.vs_output.clip)
                packed = self.__workspace._packer.pack_clip(node)
                view.outputs[tab_index] = packed
                _logger.debug("Created output node for tab %d", tab_index)

        with self.__workspace.env.use():
            view.on_current_voutput_changed(self.current_voutput, tab_index)

        if view.pixmap_item.pixmap().isNull() or refresh:
            with self.__workspace.env.use(), view.outputs[tab_index].get_frame(current_frame) as frame:
                _logger.debug("Got frame, calling on_current_frame_changed")
                view.on_current_frame_changed(current_frame, frame)
        view.setSceneRect(view.pixmap_item.boundingRect())
        view.set_autofit(view.autofit)

    def _on_current_frame_changed(self, n: int, plugin_frames: dict[str, vs.VideoFrame] | None = None) -> None:
        # Notify plugins of frame change.
        # If plugin_frames is provided, uses pre-fetched frames.
        # Otherwise, fetches frames synchronously for each plugin view.
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

    def _get_cached_settings(self, plugin: PluginBase, scope: Literal["global", "local"]) -> Any:
        cache = self.__global_settings_cache if scope == "global" else self.__local_settings_cache

        if plugin not in cache:
            if scope == "global":
                raw = SettingsManager.global_settings.plugins.setdefault(plugin.identifier, {})
                model = plugin.global_settings_model
            else:
                if self.file_path is None:
                    raw = {}
                else:
                    raw = SettingsManager.get_local_settings(self.file_path).plugins.setdefault(plugin.identifier, {})
                model = plugin.local_settings_model

            settings = model.model_validate(raw) if model is not None else raw

            if isinstance(settings, LocalSettingsModel):
                settings = settings.resolve(self._get_cached_settings(plugin, "global"))

            cache[plugin] = settings

        return cache[plugin]

    def _set_settings(self, plugin: PluginBase, scope: Literal["global", "local"], value: Any) -> None:
        if scope == "global":
            SettingsManager.global_settings.plugins[plugin.identifier] = value
            self.__global_settings_cache.pop(plugin, None)
        elif self.file_path is not None:
            SettingsManager.get_local_settings(self.file_path).plugins[plugin.identifier] = value
            self.__local_settings_cache.pop(plugin, None)

    def _on_global_settings_changed(self) -> None:
        self.__global_settings_cache.clear()
        self.globalSettingsChanged.emit()

    def _on_local_settings_changed(self, path: str) -> None:
        self.__local_settings_cache.clear()
        self.localSettingsChanged.emit(path)


class PluginBase(QWidget):
    """Base class for all plugins."""

    identifier: ClassVar[str]
    """Unique identifier for the tool."""

    display_name: ClassVar[str]
    """Display name for the tool."""

    global_settings_model: ClassVar[type[BaseModel] | None] = None
    """Pydantic model for global settings."""

    local_settings_model: ClassVar[type[BaseModel] | None] = None
    """Pydantic model for local settings."""

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api
        self.global_settings
        self.local_settings

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.api._init_plugin(self)

    @property
    def global_settings(self) -> Any:
        """Get the global settings for this plugin."""
        return self.api._get_cached_settings(self, "global")

    @global_settings.setter
    def global_settings(self, value: MutableMapping[str, Any] | BaseModel) -> None:
        self.api._set_settings(self, "global", value)

    def update_global_settings(self, **updates: Any) -> None:
        """Update specific global settings fields and trigger persistence."""
        settings = self.global_settings
        for key, value in updates.items():
            if isinstance(settings, MutableMapping):
                settings[key] = value
            else:
                setattr(settings, key, value)
        self.global_settings = settings

    @property
    def local_settings(self) -> Any:
        """Get the local settings for this plugin."""
        return self.api._get_cached_settings(self, "local")

    @local_settings.setter
    def local_settings(self, value: MutableMapping[str, Any] | BaseModel) -> None:
        self.api._set_settings(self, "local", value)

    def update_local_settings(self, **updates: Any) -> None:
        """Update specific local settings fields and trigger persistence."""
        settings = self.local_settings
        for key, value in updates.items():
            if isinstance(settings, MutableMapping):
                settings[key] = value
            else:
                setattr(settings, key, value)
        self.local_settings = settings

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """Called when the current video output changes."""

    def on_current_frame_changed(self, n: int) -> None:
        """Called when the current frame changes."""


class PluginGraphicsView(BaseGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api

        self.outputs = dict[int, vs.VideoNode]()
        self.current_tab = -1

        self.api.register_on_destroy(self.outputs.clear)

    @run_in_loop(return_future=False)
    def update_display(self, image: QPixmap) -> None:
        """Update the UI with the new image on the main thread."""
        self.pixmap_item.setPixmap(image)

    def refresh(self, plugin: PluginBase) -> None:
        """Refresh the view."""
        self.api._init_view(self, plugin, refresh=True)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """Called when the current video output changes."""

    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        """
        Called when the current frame changes.
        `n` is the frame number and `f` is the packed VideoFrame in GRAY32 format.
        """
        self.update_display(self.api.frame_to_pixmap(f))

    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        """
        Override this to transform the clip before it is displayed.
        By default, it returns the clip as-is.
        """
        return clip
