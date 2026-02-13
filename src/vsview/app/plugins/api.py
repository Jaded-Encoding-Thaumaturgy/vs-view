"""
Plugin API for VSView.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, Self, TypeVar, cast

import vapoursynth as vs
from jetpytools import copy_signature, to_arr
from pydantic import BaseModel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
    QRgba64,
    QShortcut,
)
from PySide6.QtWidgets import QGraphicsView, QWidget
from shiboken6 import Shiboken

from vsview.app.outputs import Packer, VideoOutput
from vsview.app.settings import SettingsManager, ShortcutManager
from vsview.app.settings.models import ActionDefinition
from vsview.app.views.timeline import Frame, Time
from vsview.app.views.video import BaseGraphicsView
from vsview.vsenv.loop import run_in_loop

from ._interface import _GraphicsViewProxy, _PlaybackProxy, _PluginAPI, _PluginBaseMeta, _TimelineProxy, _ViewportProxy

logger = getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VideoOutputProxy:
    """Read-only proxy for a video output."""

    vs_index: int
    """Index of the video output in the VapourSynth environment."""

    vs_name: str = field(hash=False, compare=False)
    """Name of the video output."""

    vs_output: vs.VideoOutputTuple = field(hash=False, compare=False)
    """The object created by `vapoursynth.get_outputs()`."""

    props: Mapping[int, Mapping[str, Any]] = field(hash=False, compare=False)
    """
    Frame properties of the clip.
    """

    framedurs: Sequence[float] | None = field(hash=False, compare=False)
    """
    Frame durations of the clip.
    """

    cum_durations: Sequence[float] | None = field(hash=False, compare=False)
    """
    Cumulative durations of the clip.
    """

    def time_to_frame(self, time: timedelta, fps: VideoOutputProxy | Fraction | None = None) -> Frame:
        """
        Convert a time to a frame number for this output.

        Args:
            time: The time to convert.
            fps: Optional override for FPS/duration context.
        """
        return VideoOutput.time_to_frame(self, time, fps)  # type: ignore[arg-type]

    def frame_to_time(self, frame: int, fps: VideoOutputProxy | Fraction | None = None) -> Time:
        """
        Convert a frame number to time for this output.

        Args:
            frame: The frame number to convert.
            fps: Optional override for FPS/duration context.
        """
        return VideoOutput.frame_to_time(self, frame, fps)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AudioOutputProxy:
    """Read-only proxy for an audio output."""

    vs_index: int
    """Index of the audio output in the VapourSynth environment."""

    vs_name: str = field(hash=False, compare=False)
    """Name of the audio output"""

    vs_output: vs.AudioNode = field(hash=False, compare=False)
    """The object created by `vapoursynth.get_outputs()`."""


class GraphicsViewProxy(_GraphicsViewProxy):
    """Proxy for a graphics view."""

    @property
    def pixmap(self) -> QPixmap:
        """Return the pixmap (implicitly shared)."""
        return self.__view.pixmap_item.pixmap()

    @property
    def image(self) -> QImage:
        """Return a copy of the image."""
        return self.__view.pixmap_item.pixmap().toImage()

    class ViewportProxy(_ViewportProxy):
        """Proxy for a viewport."""

        @copy_signature(QWidget().mapFromGlobal if TYPE_CHECKING else lambda *args, **kwargs: cast(Any, None))
        def map_from_global(self, *args: Any, **kwargs: Any) -> Any:
            """
            Map global coordinates to the current view's local coordinates.
            """
            return self.__viewport.mapFromGlobal(*args, **kwargs)

        def set_cursor(self, cursor: QCursor | Qt.CursorShape) -> None:
            """
            Set the cursor for the current view's viewport.
            """
            v = self.__viewport
            v.setCursor(cursor)

            if self.__cursor_reset_conn:
                self.__workspace.tab_manager.tabChanged.disconnect(self.__cursor_reset_conn)

            def reset_cursor() -> None:
                if Shiboken.isValid(v):
                    v.setCursor(Qt.CursorShape.OpenHandCursor)
                self.__cursor_reset_conn = None

            self.__cursor_reset_conn = self.__workspace.tab_manager.tabChanged.connect(
                reset_cursor,
                Qt.ConnectionType.SingleShotConnection,  # Auto-disconnects after first emit
            )

    @property
    def viewport(self) -> GraphicsViewProxy.ViewportProxy:
        """Return a proxy for the viewport."""
        return GraphicsViewProxy.ViewportProxy(self.__workspace, self.__view.viewport())

    @copy_signature(QGraphicsView().mapToScene if TYPE_CHECKING else lambda *args, **kwargs: cast(Any, None))
    def map_to_scene(self, *args: Any, **kwargs: Any) -> Any:
        """
        Map coordinates to this view's scene.
        """
        return self.__view.mapToScene(*args, **kwargs)

    @copy_signature(QGraphicsView().mapFromScene if TYPE_CHECKING else lambda *args, **kwargs: cast(Any, None))
    def map_from_scene(self, *args: Any, **kwargs: Any) -> Any:
        """
        Map coordinates from view's scene.
        """
        return self.__view.mapFromScene(*args, **kwargs)


class TimelineProxy(_TimelineProxy):
    """Proxy for the timeline."""

    @staticmethod
    def _norm_data(
        data: timedelta | int | Sequence[timedelta | int | tuple[timedelta | int, timedelta | int]],
    ) -> Iterator[tuple[Frame | Time, Frame | Time | None]]:
        data = [data] if isinstance(data, (timedelta, int)) else data

        for d in data:
            if isinstance(d, (timedelta, int)):
                start, end = d, None
            else:
                start, end = d

            start, end = (
                Frame(start) if isinstance(start, int) else Time(seconds=start.seconds),
                Frame(end)
                if isinstance(end, int)
                else Time(seconds=end.seconds)
                if isinstance(end, timedelta)
                else None,
            )
            yield start, end

    @property
    def mode(self) -> Literal["frame", "time"]:
        """Returns the Timeline mode, "frame" or "time"."""
        return self.__timeline.mode

    def add_notch(
        self,
        identifier: str,
        data: timedelta | int | Sequence[timedelta | int | tuple[timedelta | int, timedelta | int]],
        color: Qt.GlobalColor | QColor | QRgba64 | str | int = Qt.GlobalColor.black,
        label: str = "",
        notch_id: Hashable | None = None,
        *,
        update: bool = True,
    ) -> None:
        """
        Add notch(es) to the timeline.

        Args:
            identifier: The identifier of the notch.
            data: The data of the notch (frame number or timedelta or range notches).
            color: The color of the notch.
            label: The label of the notch.
            notch_id: The ID of the notch.
            update: Whether to update the timeline.
        """
        for start, end in self._norm_data(data):
            self.__timeline.add_notch(identifier, start, end, color=color, label=label, id=notch_id)

        if update:
            self.__timeline.update()

    def discard_notch(
        self,
        identifier: str,
        data: timedelta | int | Sequence[timedelta | int | tuple[timedelta | int, timedelta | int]],
        notch_id: Hashable | None = None,
        *,
        update: bool = True,
    ) -> None:
        """
        Discard notch(es) from the timeline.

        Args:
            identifier: The identifier of the notch.
            data: The data of the notch (frame number or timedelta or range notches).
            notch_id: The ID of the notch.
            update: Whether to update the timeline.
        """
        for start, end in self._norm_data(data):
            self.__timeline.discard_notch(identifier, start, end, notch_id)

        if update:
            self.__timeline.update()

    def clear_notches(self, identifier: str | Iterable[str], *, update: bool = True) -> None:
        """
        Clear all notches for a given identifier.

        Args:
            identifier: The identifier of the notches to clear.
            update: Whether to update the timeline.
        """
        for i in to_arr(identifier):
            self.__timeline.custom_notches.pop(i, None)

        if update:
            self.__timeline.update()

    def update(self) -> None:
        """
        Update the timeline.
        """
        self.__timeline.update()


class PlaybackProxy(_PlaybackProxy):
    """Proxy for the playback."""

    def seek(self, frame: int) -> bool:
        """
        Seek to the given frame.

        Args:
            frame: The frame number to seek to.

        Returns:
            bool: True if the seek was successful, False otherwise.
        """
        if self.__workspace.playback.state.is_playing:
            logger.debug("Video is playing, skipping seek request")
            return False

        if not 0 <= frame < self.__workspace.api.current_voutput.vs_output.clip.num_frames:
            logger.warning("Requested frame is out of bounds")
            return False

        self.__workspace.playback.request_frame(frame)

        return True


class PluginAPI(_PluginAPI):
    """API for plugins to interact with the workspace."""

    if TYPE_CHECKING:
        statusMessage = Signal(str)  # message
        """Signal to emit status messages."""

        globalSettingsChanged = Signal()
        """Signal to emit when global settings change."""

        localSettingsChanged = Signal(str)
        """Signal to emit when local settings change."""

    @property
    def file_path(self) -> Path | None:
        """Return the file path of the currently loaded file, or None if not a file."""
        return self._settings_store.file_path

    @property
    def current_frame(self) -> Frame:
        """Return the current frame number."""
        return Frame(self.__workspace.playback.state.current_frame)

    @property
    def current_time(self) -> Time:
        """Return the current time."""
        if voutput := self.__workspace.outputs_manager.current_voutput:
            return voutput.frame_to_time(self.current_frame)

        raise NotImplementedError

    @property
    def current_video_index(self) -> int:
        """Return the index of the currently selected tab."""
        return self.__workspace.outputs_manager.current_video_index

    if TYPE_CHECKING:

        @property
        def voutputs(self) -> list[VideoOutputProxy]:
            """Return a dictionary of VideoOutputProxy objects for all tabs."""
            ...

        @property
        def current_voutput(self) -> VideoOutputProxy:
            """Return the VideoOutput for the currently selected tab."""
            ...

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        """Return a list of AudioOutputProxy objects."""
        return [
            AudioOutputProxy(aoutput.vs_index, aoutput.vs_name, aoutput.vs_output)
            for aoutput in self.__workspace.outputs_manager.aoutputs
        ]

    @property
    def current_aoutput(self) -> AudioOutputProxy | None:
        if aoutput := self.__workspace.outputs_manager.current_aoutput:
            return AudioOutputProxy(aoutput.vs_index, aoutput.vs_name, aoutput.vs_output)

        return None

    @property
    def is_playing(self) -> bool:
        """Return whether playback is currently active."""
        return self.__workspace.playback.state.is_playing

    @property
    def packer(self) -> Packer:
        """Return the packer used by the workspace."""
        return self.__workspace.outputs_manager.packer

    @property
    def current_view(self) -> GraphicsViewProxy:
        """Return a proxy for the current view."""
        return GraphicsViewProxy(self.__workspace, self.__workspace.tab_manager.current_view)

    @property
    def timeline(self) -> TimelineProxy:
        """Return a proxy for the timeline."""
        return TimelineProxy(self.__workspace, self.__workspace.tbar.timeline)

    @property
    def playback(self) -> PlaybackProxy:
        """Return a proxy for the playback."""
        return PlaybackProxy(self.__workspace, self.__workspace.playback)

    def get_local_storage(self, plugin: _PluginBase[Any, Any]) -> Path | None:
        """
        Return a path to a local storage directory for the given plugin,
        or None if the current workspace has no file path.
        """
        if not self.file_path:
            return None

        settings_path = SettingsManager.local_settings_path(self.file_path)
        local_storage = settings_path.with_suffix("").with_stem(settings_path.stem.upper()) / plugin.identifier
        local_storage.mkdir(parents=True, exist_ok=True)

        return local_storage

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        """
        Register a callback to be called before the workspace begins a reload or when the workspace is destroyed.
        This is generaly used to clean up VapourSynth resources.
        """
        self.__workspace.cbs_on_destroy.append(cb)

    @contextmanager
    def vs_context(self) -> Iterator[None]:
        """
        Context manager for using the VapourSynth environment of the workspace.
        """
        with self.__workspace.env.use():
            yield

    def register_action(
        self,
        action_id: str,
        action: QAction,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> None:
        """
        Register a QAction for shortcut management.

        Args:
            action_id: The namespaced identifier (e.g., "my_plugin.do_thing").
            action: The QAction to manage.
            context: The context in which the shortcut should be active.
        """
        ShortcutManager.register_action(action_id, action, context=context)

    def register_shortcut(
        self,
        action_id: str,
        callback: Callable[[], Any],
        parent: QWidget,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> QShortcut:
        """
        Create and register a QShortcut for shortcut management.

        Args:
            action_id: The namespaced identifier (e.g., "my_plugin.do_thing").
            callback: The function to call when the shortcut is activated.
            parent: The parent widget that determines shortcut scope.
            context: The context in which the shortcut should be active.

        Returns:
            The created QShortcut instance.
        """
        return ShortcutManager.register_shortcut(action_id, callback, parent, context=context)


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


if sys.version_info >= (3, 13):
    TGlobalSettings = TypeVar("TGlobalSettings", bound=BaseModel | None, default=None)
    TLocalSettings = TypeVar("TLocalSettings", bound=BaseModel | None, default=None)
    NodeT = TypeVar("NodeT", bound=vs.RawNode)
else:
    import typing_extensions

    TGlobalSettings = typing_extensions.TypeVar("TGlobalSettings", bound=BaseModel | None, default=None)
    TLocalSettings = typing_extensions.TypeVar("TLocalSettings", bound=BaseModel | None, default=None)
    NodeT = typing_extensions.TypeVar("NodeT", bound=vs.RawNode)


class PluginSettings(Generic[TGlobalSettings, TLocalSettings]):
    """
    Settings wrapper providing lazy, always-fresh access.

    Returns None if no settings model is defined for the scope.
    """

    def __init__(self, plugin: _PluginBase[TGlobalSettings, TLocalSettings]) -> None:
        self._plugin = plugin

    @property
    def global_(self) -> TGlobalSettings:
        """Get the current global settings."""
        return self._plugin.api._get_cached_proxy_settings(self._plugin, "global")

    @property
    def local_(self) -> TLocalSettings:
        """Get the current local settings (resolved with global fallbacks)."""
        return self._plugin.api._get_cached_proxy_settings(self._plugin, "local")


class _PluginBase(Generic[TGlobalSettings, TLocalSettings], metaclass=_PluginBaseMeta):  # noqa: UP046
    __plugin_base__ = True

    identifier: ClassVar[str]
    """Unique identifier for the plugin."""

    display_name: ClassVar[str]
    """Display name for the plugin."""

    shortcuts: ClassVar[Sequence[ActionDefinition]] = ()
    """
    Keyboard shortcuts for this plugin.

    Each ActionDefinition ID must start with "{identifier}." prefix.
    """

    def __init__(self, api: PluginAPI, /) -> None:
        self.api = api

    @property
    def settings(self) -> PluginSettings[TGlobalSettings, TLocalSettings]:
        """Get the settings wrapper for lazy, always-fresh access."""
        return PluginSettings(self)

    def update_global_settings(self, **updates: Any) -> None:
        """Update specific global settings fields and trigger persistence."""
        self.api._update_settings(self, "global", **updates)

    def update_local_settings(self, **updates: Any) -> None:
        """Update specific local settings fields and trigger persistence."""
        self.api._update_settings(self, "local", **updates)


class WidgetPluginBase(_PluginBase[TGlobalSettings, TLocalSettings], QWidget, metaclass=_PluginBaseMeta):
    """Base class for all widget plugins."""

    __plugin_base__ = True

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        QWidget.__init__(self, parent)
        self.api = api
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the current video output changes.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """
        self.on_current_frame_changed(self.api.current_frame)

    def on_current_frame_changed(self, n: int) -> None:
        """
        Called when the current frame changes.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """

    def on_playback_started(self) -> None:
        """
        Called when playback starts.

        Execution Thread: **Main**.
        """

    def on_playback_stopped(self) -> None:
        """
        Called when playback stops.

        Execution Thread: **Main**.
        """

    def on_view_context_menu(self, event: QContextMenuEvent) -> None:
        """
        Called when a context menu of the current viewis requested.

        The event is forwarded BEFORE the view processes it.

        Execution Thread: **Main**.
        """

    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        """
        Called when the mouse of the current view is moved.

        The event is forwarded AFTER the view processes it.

        Execution Thread: **Main**.
        """

    def on_view_mouse_pressed(self, event: QMouseEvent) -> None:
        """
        Called when the mouse of the current view is pressed.

        The event is forwarded AFTER the view processes it.

        Execution Thread: **Main**.
        """

    def on_view_mouse_released(self, event: QMouseEvent) -> None:
        """
        Called when the mouse of the current view is released.

        The event is forwarded AFTER the view processes it.

        Execution Thread: **Main**.
        """

    def on_view_key_press(self, event: QKeyEvent) -> None:
        """
        Called when a key is pressed in the current view.

        The event is forwarded AFTER the view processes it.

        Execution Thread: **Main**.
        """

    def on_view_key_release(self, event: QKeyEvent) -> None:
        """
        Called when a key is released in the current view.

        The event is forwarded AFTER the view processes it.

        Execution Thread: **Main**.
        """

    def on_hide(self) -> None:
        """
        Called when the plugin is hidden.

        Execution Thread: **Main**.
        """


class PluginGraphicsView(BaseGraphicsView):
    """Graphics view for plugins."""

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api

        self.outputs = dict[int, vs.VideoNode]()
        self.current_tab = -1
        self.last_frame = -1

        self.api.register_on_destroy(self.outputs.clear)

    @run_in_loop(return_future=False)
    def update_display(self, image: QImage) -> None:
        """Update the UI with the new image on the main thread."""
        self.set_pixmap(QPixmap.fromImage(image))

    def refresh(self) -> None:
        """Refresh the view."""
        self.api._init_view(self, refresh=True)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the current video output changes.

        **Warning**: Do not call `self.refresh()` here, as it will cause an infinite loop.
        If you need to update the display manually, use `self.update_display()`.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """

    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        """
        Called when the current frame changes.
        `n` is the frame number and `f` is the packed VideoFrame in GRAY32 format.

        **Warning**: Do not call `self.refresh()` here, as it will cause an infinite loop.
        If you need to update the display manually, use `self.update_display()`.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """
        self.update_display(self.api.packer.frame_to_qimage(f).copy())

    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        """
        Override this to transform the clip before it is displayed.
        By default, it returns the clip as-is.
        """
        return clip


# Node Processing Hooks
class NodeProcessor(
    _PluginBase[TGlobalSettings, TLocalSettings],
    Generic[NodeT, TGlobalSettings, TLocalSettings],  # noqa: UP046
    metaclass=_PluginBaseMeta,
):
    """Interface for objects that process VapourSynth nodes."""

    __plugin_base__ = True

    def prepare(self, node: NodeT, /) -> NodeT:
        """
        Process the input node and return a modified node of the same type.

        Args:
            node: The raw input node (VideoNode or AudioNode).

        Returns:
            The processed node compatible with the player's output requirements.
        """
        raise NotImplementedError
