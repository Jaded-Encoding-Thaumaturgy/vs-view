"""
Plugin API for VSView.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator, Hashable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from logging import getLogger
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeVar, cast, override

import vapoursynth as vs
from jetpytools import copy_signature, to_arr
from pydantic import BaseModel
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QCursor,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QRgba64,
    QShortcut,
)
from PySide6.QtWidgets import QGraphicsView, QWidget
from shiboken6 import Shiboken

from vsview.app.packing import Packer
from vsview.app.settings import SettingsManager, ShortcutManager
from vsview.app.settings.action import ActionDefinition
from vsview.app.views.video import BaseGraphicsView
from vsview.types import Frame, Time
from vsview.vsenv.loop import run_in_loop

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from typing_extensions import deprecated


from ._interface import (
    _GraphicsViewProxy,
    _PlaybackProxy,
    _PluginAPI,
    _PluginBaseMeta,
    _PluginSecrets,
    _TimelineProxy,
    _ViewportProxy,
)
from .contracts import AudioOutputProxy, VideoOutputProxy

logger = getLogger(__name__)


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

    @property
    def cursor_pos(self) -> QPointF:
        """
        Return the current cursor position in scene coordinates.
        """
        return self.map_to_scene(self.viewport.cursor_pos)

    @property
    def rect_selection_enabled(self) -> bool:
        """Return whether rectangular selection editing is enabled."""
        return self.__view.rect_selection_enabled

    @rect_selection_enabled.setter
    def rect_selection_enabled(self, enabled: bool) -> None:
        """Enable or disable editing of the view's rectangular selection."""
        self.__view.rect_selection_enabled = enabled

    @property
    def rect_selection(self) -> QRect:
        """Return the current rectangular selection in source image pixel coordinates."""
        return self.__view.rect_selection

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        """Set the current rectangular selection in source image pixel coordinates."""
        self.__view.set_rect_selection(rect, finished=finished)

    def clear_rect_selection(self) -> None:
        """Clear the current rectangular selection."""
        self.__view.clear_rect_selection()

    class ViewportProxy(_ViewportProxy):
        """Proxy for a viewport."""

        @property
        def cursor_pos(self) -> QPoint:
            """
            Return the current cursor position in viewport coordinates.
            """
            return self.map_from_global(QCursor.pos())

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

    def map_to_image(self, point: QPoint | QPointF) -> QPointF:
        """
        Map coordinates from viewport to the source image's pixel coordinate space.
        """
        return self.__view.map_to_image(point)


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
                Frame(start) if isinstance(start, int) else Time(seconds=start.total_seconds()),
                Frame(end)
                if isinstance(end, int)
                else Time(seconds=end.total_seconds())
                if isinstance(end, timedelta)
                else None,
            )
            yield start, end

    @property
    def mode(self) -> Literal["frame", "time"]:
        """Returns the current timeline display mode."""
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
        Add one or more notches to the timeline.

        Args:
            identifier: A string identifier for the group of notches.
            data: The position(s) of the notch(es).
            color: The color of the notch markers. Defaults to black.
            label: An optional label to display with the notch.
            notch_id: An optional hashable ID to uniquely identify this specific notch.
            update: If True (default), triggers a visual update of the timeline.
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
        Remove specific notch(es) from the timeline that match the given criteria.

        Args:
            identifier: The group identifier of the notches to remove.
            data: The position(s) of the notch(es) to remove.
            notch_id: If provided, only removes notches matching this specific ID.
            update: If True (default), triggers a visual update of the timeline.
        """
        for start, end in self._norm_data(data):
            self.__timeline.discard_notch(identifier, start, end, notch_id)

        if update:
            self.__timeline.update()

    def clear_notches(self, identifier: str | Iterable[str], *, update: bool = True) -> None:
        """
        Clear all notches associated with the given identifier(s).

        Args:
            identifier: A single identifier or an iterable of identifiers to clear.
            update: If True (default), triggers a visual update of the timeline.
        """
        for i in to_arr(identifier):
            self.__timeline.custom_notches.pop(i, None)

        if update:
            self.__timeline.update()

    def update(self) -> None:
        """
        Manually trigger a visual refresh of the timeline.
        """
        self.__timeline.update()


class PlaybackProxy(_PlaybackProxy):
    """Proxy for the playback."""

    def seek(self, frame_or_time: int | timedelta, /) -> bool:
        """
        Seek to the given frame or time.

        Args:
            frame_or_time: The frame number or time to seek to.

        Returns:
            bool: True if the seek was successful, False otherwise.
        """
        if self.__workspace.playback.state.is_playing:
            logger.debug("Video is playing, skipping seek request")
            return False

        if isinstance(frame_or_time, timedelta):
            frame = self.__workspace.api.current_voutput.time_to_frame(frame_or_time)
        else:
            frame = frame_or_time

        if not 0 <= frame < self.__workspace.api.current_voutput.vs_output.clip.num_frames:
            logger.warning("Requested frame is out of bounds")
            return False

        self.__workspace.playback.request_frame(frame)

        return True


class PluginSecrets(_PluginSecrets):
    """
    Secure secret storage API for plugins, backed by the OS keyring.

    The `keyring` package is necessary for this API to work. You can depend on it with `vsview[secrets]`.
    """

    if TYPE_CHECKING:
        from keyring.credentials import Credential

        def get(self, context: str, username: str) -> str | None:
            """
            Get a plaintext secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: The unique identifier or key for the secret.

            Returns:
                The secret string if found, otherwise None.
            """

        def set(self, context: str, username: str, password: str) -> None:
            """
            Set a plaintext secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: The unique identifier or key for the secret.
                password: The plaintext secret value to store.
            """

        def delete(self, context: str, username: str) -> None:
            """
            Delete a secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: The unique identifier or key for the secret.
            """

        def get_credential(self, context: str, username: str | None = None) -> Credential | None:
            """
            Get a credential (username/password pair) for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: Optional filter for a specific username.

            Returns:
                A Credential object if found, otherwise None.
            """

        def set_credential(self, context: str, username: str, password: str) -> None:
            """
            Set a credential (username/password pair) for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: The unique identifier or key for the secret.
                password: The plaintext secret value to store.
            """

        def delete_credential(self, context: str, username: str) -> None:
            """
            Delete a credential (username/password pair) for the given key.

            Args:
                context: A sub-category or context for the secret.
                username: The unique identifier or key for the secret.
            """

        def get_json(self, context: str, key: str) -> Any | None:
            """
            Get a JSON-decoded secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                key: The unique identifier for the secret.

            Returns:
                The decoded JSON data if found, otherwise None.
            """

        def set_json(self, context: str, key: str, value: Any) -> None:
            """
            Set a JSON-encoded secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                key: The unique identifier for the secret.
                value: Any JSON-serializable data to store.
            """

        def delete_json(self, context: str, key: str) -> None:
            """
            Delete a JSON-encoded secret value for the given key.

            Args:
                context: A sub-category or context for the secret.
                key: The unique identifier for the secret.
            """


class PluginAPI(_PluginAPI):
    """API for plugins to interact with the workspace."""

    if TYPE_CHECKING:
        statusMessage = Signal(str)  # message
        """Signal to emit status messages."""

        globalSettingsChanged = Signal()
        """Signal to emit when global settings change."""

        localSettingsChanged = Signal(str)
        """Signal to emit when local settings change."""

        aboutToSaveGlobal = Signal()
        """Signal to emit before global settings are saved."""

        aboutToSaveLocal = Signal(str)  # path
        """Signal to emit before local settings are saved."""

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
        @override
        def voutputs(self) -> list[VideoOutputProxy]:
            """Return a dictionary of VideoOutputProxy objects for all tabs."""
            ...

        @property
        @override
        def current_voutput(self) -> VideoOutputProxy:
            """Return the VideoOutput for the currently selected tab."""
            ...

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        """Return a list of AudioOutputProxy objects."""
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
        """Return whether playback is currently active."""
        return self.__workspace.playback.state.is_playing

    @property
    @deprecated(
        "Accessing this property is deprecated. "
        "Use `self.api.current_voutput.packer` or `self.api.current_voutput.packer_sdr`",
        category=DeprecationWarning,
    )
    def packer(self) -> Packer:
        """Return the packer used by the workspace."""
        return self.current_voutput.packer_sdr

    @property
    def settings(self) -> SimpleNamespace:
        """
        !!! warning "Unstable API"
            Return the application's global and local settings as nested SimpleNamespace proxy objects.
        """

        def recursive_ns(data: dict[str, Any]) -> SimpleNamespace:
            return SimpleNamespace(**{k: recursive_ns(v) if isinstance(v, dict) else v for k, v in data.items()})

        global_ = SettingsManager.global_settings.model_copy(deep=True).model_dump(exclude={"plugins"})
        local_ = (
            SettingsManager.get_local_settings(p).model_copy(deep=True).model_dump(exclude={"plugins"})
            if (p := self._settings_store.file_path)
            else {}
        )
        settings = SimpleNamespace()
        settings.global_ = recursive_ns(global_)
        settings.local_ = recursive_ns(local_)

        return settings

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

    @property
    def busy(self) -> bool:
        """Return whether the plugin API is busy by any plugins."""
        return bool(self.__busy_callers)

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
    def vs_context(self) -> Generator[None]:
        """
        Context manager for using the VapourSynth environment of the workspace.
        """
        with self.__workspace.env.use():
            yield

    @contextmanager
    def block_workspace(self, caller: WidgetPluginBase[Any, Any]) -> Generator[None]:
        """
        Mark the workspace as busy for the duration of the context.

        Specifically, a busy workspace will prevent:

        * Automatic or manual reloading.
        * Frame requests (via seeking, programmatic calls, or playback start).
        """
        self.__busy_callers.add(caller)

        try:
            yield
        finally:
            self.__busy_callers.discard(caller)

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

    def get_shortcut_label(self, action_id: str) -> str:
        """
        Return the current shortcut's native display string or an empty string if no shortcut is assigned.
        """
        key = ShortcutManager.get_key(action_id)
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText) if key else ""


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


class _PluginBase(Generic[TGlobalSettings, TLocalSettings], metaclass=_PluginBaseMeta):
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

    @property
    def secrets(self) -> PluginSecrets:
        """Get a namespaced secure secrets API for this plugin."""
        return PluginSecrets(self)

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

    def on_view_rect_selection_changed(self, rect: QRect) -> None:
        """
        Called when the current view's rectangular selection changes.

        `rect` is in source image pixel coordinates. An empty rect means the selection was cleared.

        Execution Thread: **Main**.
        """

    def on_view_rect_selection_finished(self, rect: QRect) -> None:
        """
        Called when the current view's rectangular selection interaction finishes.

        `rect` is in source image pixel coordinates. An empty rect means the selection was cleared.

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
        self.update_display(self.api.current_voutput.packer_sdr.frame_to_qimage(f).copy())

    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        """
        Override this to transform the clip before it is displayed.
        By default, it returns the clip as-is.
        """
        return clip


# Node Processing Hooks
class NodeProcessor(
    _PluginBase[TGlobalSettings, TLocalSettings],
    Generic[NodeT, TGlobalSettings, TLocalSettings],
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
