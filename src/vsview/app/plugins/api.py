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
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, Protocol, TypeVar, cast, runtime_checkable

import vapoursynth as vs
from jetpytools import copy_signature, to_arr
from pydantic import BaseModel
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, Signal
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

from vsview.app.settings import SettingsManager, ShortcutManager
from vsview.app.settings.action import ActionDefinition
from vsview.app.views.video import BaseGraphicsView
from vsview.types import Frame, Time
from vsview.vsenv.loop import run_in_loop

from ._controller import get_controller as _get_controller
from ._interface import (
    _GraphicsViewProxy,
    _PlaybackProxy,
    _PluginBaseMeta,
    _PluginLike,
    _PluginSecrets,
    _TimelineProxy,
    _ViewportProxy,
)
from .contracts import AudioOutputProxy, VideoOutputProxy
from .exceptions import NoCurrentVideoOutputError

logger = getLogger(__name__)


class GraphicsViewProxy(_GraphicsViewProxy):
    """
    Proxy for a graphics view associated with an active video tab.

    Instances are transient and scoped to the currently active tab.
    Do not cache `api.current_view` across tab switches, always query it fresh.
    """

    @property
    def pixmap(self) -> QPixmap:
        """Return the pixmap (implicitly shared). Must be called on the main GUI thread."""
        return self.__view.pixmap_item.pixmap()

    @property
    def image(self) -> QImage:
        """
        Return a copy of the image.

        Must be called on the main GUI thread because it accesses the underlying QGraphicsPixmapItem.
        """
        return self.__view.pixmap_item.pixmap().toImage()

    @property
    def cursor_pos(self) -> QPointF:
        """
        Return the current cursor position in scene coordinates (floating-point).

        To map to source image pixel coordinates, use `map_to_image(self.viewport.cursor_pos)`.
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
            Return the current cursor position in viewport coordinates (integer pixels).
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

            Note:
                The cursor automatically resets to `Qt.CursorShape.OpenHandCursor` when switching video tabs.
            """
            v = self.__viewport
            v.setCursor(cursor)

            if self.__cursor_reset_conn:
                self.__tab_changed.disconnect(self.__cursor_reset_conn)

            def reset_cursor() -> None:
                if Shiboken.isValid(v):
                    v.setCursor(Qt.CursorShape.OpenHandCursor)
                self.__cursor_reset_conn = None

            self.__cursor_reset_conn = self.__tab_changed.connect(
                reset_cursor,
                Qt.ConnectionType.SingleShotConnection,  # Auto-disconnects after first emit
            )

    @property
    def viewport(self) -> GraphicsViewProxy.ViewportProxy:
        """Return a proxy for the viewport."""
        return GraphicsViewProxy.ViewportProxy(self.__tab_changed, self.__view.viewport())

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

        Args:
            point: The point in viewport or widget local coordinates
                (e.g. from `self.viewport.cursor_pos` or `QMouseEvent.position()`).
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
        Add one or more notches or range spans to the timeline.

        Args:
            identifier: A string identifier for the group of notches.
            data: The position(s) or range(s) of the notch(es).
                Accepts a single frame/time, a sequence of positions, or `(start, end)` tuples for range spans.
            color: The color of the notch markers. Defaults to black.
            label: An optional label to display with the notch.
            notch_id: An optional hashable ID to uniquely identify this specific notch within the group.
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
            data: The position(s) or range(s) of the notch(es) to remove.
                Note that `data` must match the notch's position even when `notch_id` is supplied.
            notch_id: Optional hashable ID used when adding the notch.
            update: If True (default), triggers a visual update of the timeline.
        """
        for start, end in self._norm_data(data):
            self.__timeline.discard_notch(identifier, start, end, notch_id)

        if update:
            self.__timeline.update()

    def clear_notches(self, identifier: str | Iterable[str], *, update: bool = True) -> None:
        """
        Clear all notches associated with the given group identifier(s).

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
    """Proxy for controlling playback position."""

    def seek(self, frame_or_time: int | timedelta, /) -> bool:
        """
        Seek to the given frame or time.

        Args:
            frame_or_time: The frame number or time to seek to.

        Returns:
            bool: True if the seek was initiated, False if playback is currently active,
                the workspace is busy/blocked by a WorkspaceBlocker, or the frame is out of bounds.

        Raises:
            NoCurrentVideoOutputError: If no video output is currently available in the active workspace.
        """
        if self.__playback_manager.state.is_playing:
            logger.debug("Video is playing, skipping seek request")
            return False

        if not (v := self.__playback_manager._outputs_manager.current_voutput):
            raise NoCurrentVideoOutputError("No video output is currently available in the active workspace.")

        frame = v.time_to_frame(frame_or_time) if isinstance(frame_or_time, timedelta) else frame_or_time

        if not 0 <= frame < v.vs_output.clip.num_frames:
            logger.warning("Requested frame is out of bounds")
            return False

        return self.__playback_manager.request_frame(frame)


class PluginSecrets(_PluginSecrets):
    """
    Secure secret storage API for plugins, backed by the OS keyring.

    All stored secrets are automatically scoped and namespaced to the plugin's `identifier`.
    The `keyring` package is necessary for this API to work (`vsview[secrets]`).
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


class PluginAPI(QObject):
    """API for plugins to interact with the workspace."""

    statusMessage = Signal(str)  # message
    """Signal to emit status messages displayed in the main window status bar."""

    globalSettingsChanged = Signal()
    """Signal emitted when global settings change or are invalidated."""

    localSettingsChanged = Signal(str)
    """Signal emitted when local settings change, passing the affected script file path."""

    aboutToSaveGlobal = Signal()
    """Signal emitted immediately before global settings are saved to disk."""

    aboutToSaveLocal = Signal(str)  # path
    """Signal emitted immediately before local settings are saved to disk, passing the script file path."""

    @property
    def file_path(self) -> Path | None:
        """Return the file path of the currently loaded file, or None if not a file."""
        return _get_controller(self).file_path

    @property
    def current_frame(self) -> Frame:
        """Return the current frame number."""
        return _get_controller(self).current_frame

    @property
    def current_time(self) -> Time:
        """
        Return the current playback time.

        Raises:
            NoCurrentVideoOutputError: If no video output is currently available in the active workspace.
        """
        return _get_controller(self).current_time

    @property
    def current_video_index(self) -> int:
        """Return the index of the currently selected tab."""
        return _get_controller(self).current_video_index

    @property
    def voutputs(self) -> list[VideoOutputProxy]:
        """Return a list of VideoOutputProxy objects for all tabs."""
        return _get_controller(self).voutputs

    @property
    def current_voutput(self) -> VideoOutputProxy:
        """
        Return the VideoOutputProxy for the currently selected tab.

        Raises:
            NoCurrentVideoOutputError: If no video output is currently available in the active workspace.
        """
        return _get_controller(self).current_voutput

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        """Return a list of AudioOutputProxy objects."""
        return _get_controller(self).aoutputs

    @property
    def current_aoutput(self) -> AudioOutputProxy | None:
        """Return the AudioOutputProxy for the current audio stream, or None if no audio output exists."""
        return _get_controller(self).current_aoutput

    @property
    def is_playing(self) -> bool:
        """Return whether playback is currently active."""
        return _get_controller(self).is_playing

    @property
    def settings(self) -> SimpleNamespace:
        """
        Warning: Unstable API
            Return a read-only snapshot of the application's global and local settings
            as nested SimpleNamespace proxy objects (excluding plugin settings).

        Note:
            To access and persist your own plugin's settings, use `self.settings` on
            `WidgetPluginBase` or `NodeProcessor` instead of `api.settings`.
        """

        def recursive_ns(data: dict[str, Any]) -> SimpleNamespace:
            return SimpleNamespace(**{k: recursive_ns(v) if isinstance(v, dict) else v for k, v in data.items()})

        global_ = SettingsManager.global_settings.model_copy(deep=True).model_dump(exclude={"plugins"})
        local_ = (
            SettingsManager.get_local_settings(p).model_copy(deep=True).model_dump(exclude={"plugins"})
            if (p := self.file_path)
            else {}
        )
        settings = SimpleNamespace()
        settings.global_ = recursive_ns(global_)
        settings.local_ = recursive_ns(local_)

        return settings

    @property
    def current_view(self) -> GraphicsViewProxy:
        """
        Return a transient proxy for the current tab's graphics view.

        Raises:
            NoCurrentViewError: If no graphics view is currently available in the active workspace.
        """
        return _get_controller(self).current_view_proxy

    @property
    def timeline(self) -> TimelineProxy:
        """Return a proxy for the timeline."""
        return _get_controller(self).timeline_proxy

    @property
    def playback(self) -> PlaybackProxy:
        """Return a proxy for the playback."""
        return _get_controller(self).playback_proxy

    @property
    def busy(self) -> bool:
        """Return whether any plugin currently holds an active WorkspaceBlocker."""
        return _get_controller(self).busy

    def get_local_storage(self, plugin: _PluginBase[Any, Any]) -> Path | None:
        """
        Return a directory path for persistent plugin storage next to the loaded script,
        or None if the current workspace has no file path.

        Note:
            The directory is automatically created on disk if it does not already exist.
        """
        return _get_controller(self).get_local_storage(plugin)

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        """
        Register a callback to be called before the workspace begins a reload or when the workspace is destroyed.
        This is generally used to clean up VapourSynth resources.
        """
        _get_controller(self).register_on_destroy(cb)

    @contextmanager
    def vs_context(self) -> Generator[None]:
        """
        Context manager activating the workspace's managed VapourSynth environment.

        Required when creating clips, filtering, or evaluating frames outside standard callbacks
        such as `PluginGraphicsView.get_node` or `NodeProcessor.prepare`.
        """
        with _get_controller(self).vs_context():
            try:
                yield
            finally:
                pass

    def blocker(self, caller: WidgetPluginBase[Any, Any] | None = None) -> WorkspaceBlocker:
        """
        Create a WorkspaceBlocker to mark the workspace as busy.

        Args:
            caller: Optional widget holding the blocker.
                When supplied, the lock is tracked against the caller's lifetime and released if destroyed.
        """
        return _get_controller(self).create_blocker(self, caller)

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

    def get_shortcut_key_sequence(self, action_id: str) -> QKeySequence:
        """
        Return the key sequence currently assigned to a registered action.

        Args:
            action_id: The namespaced identifier (e.g., "my_plugin.do_thing").

        Returns:
            The assigned QKeySequence, or an empty QKeySequence if no shortcut is assigned.
        """
        return ShortcutManager.get_key(action_id)

    def get_shortcut_label(self, action_id: str) -> str:
        """
        Return the current shortcut's native display string or an empty string if no shortcut is assigned.
        """
        key = ShortcutManager.get_key(action_id)
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText) if key else ""


@runtime_checkable
class WorkspaceBlocker(Protocol):
    """
    A lock-like context manager to block the workspace.

    When the blocker is acquired, mark the workspace as busy for the duration of the context.

    Specifically, a busy workspace will prevent:

    * Automatic or manual reloading.
    * Frame requests (via seeking, programmatic calls, or playback start).
    """

    def __enter__(self) -> None: ...
    def __exit__(self, *_: object) -> None: ...

    @property
    def locked(self) -> bool:
        """
        Return whether the lock is currently acquired.
        """
        ...

    def acquire(self, block: bool = True, timeout: float | None = None) -> bool:
        """
        Acquire the lock to block the workspace.
        """
        ...

    def release(self) -> None:
        """
        Release the lock to unblock the workspace.
        """


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
    Settings wrapper providing lazy, live access to plugin configuration models.

    Direct attribute assignments (e.g., `self.settings.global_.key = value`)
    are automatically intercepted and persisted to disk.

    Note:
        In-place mutations of mutable containers (e.g., `self.settings.global_.items.append(...)`)
        do not trigger `__setattr__` and are not persisted automatically. Reassign the attribute
        or use `update_global_settings()` / `update_local_settings()`.

        When local settings inherit from `LocalSettingsModel`, `self.settings.local_` automatically
        resolves with global fallbacks applied for any `None` values. Returns None if no settings
        model is defined for the scope.
    """

    def __init__(self, plugin: _PluginLike) -> None:
        self._plugin = plugin

    @property
    def global_(self) -> TGlobalSettings:
        """Get the current global settings."""
        return _get_controller(self._plugin.api).get_cached_proxy_settings(self._plugin, "global")  # type: ignore[return-value]

    @property
    def local_(self) -> TLocalSettings:
        """Get the current local settings (resolved with global fallbacks)."""
        return _get_controller(self._plugin.api).get_cached_proxy_settings(self._plugin, "local")  # type: ignore[return-value]


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
        _get_controller(self.api).update_settings(self, "global", **updates)

    def update_local_settings(self, **updates: Any) -> None:
        """Update specific local settings fields and trigger persistence."""
        _get_controller(self.api).update_settings(self, "local", **updates)


class WidgetPluginBase(_PluginBase[TGlobalSettings, TLocalSettings], QWidget, metaclass=_PluginBaseMeta):
    """
    Base class for all widget plugins (tool panels and tool docks).

    Note: Visibility Gating
        Lifecycle and interaction hooks are only called when the widget is truly visible
        (not hidden, collapsed in a splitter, or placed on an inactive dock tab).

        When a hidden plugin becomes visible again, `on_current_voutput_changed` is invoked automatically.
    """

    __plugin_base__ = True

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        QWidget.__init__(self, parent)
        self.api = api
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def on_workspace_loaded(self) -> None:
        """
        Called when the workspace has loaded or reloaded content.

        Execution Thread: **Background**.

        Any UI updates MUST use the `@run_in_loop` decorator.
        """

    def on_workspace_destroy(self) -> None:
        """
        Called when the workspace is being destroyed.

        Execution Thread: **Background**.

        Any UI updates MUST use the `@run_in_loop` decorator.
        """

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the active video output tab changes, or when the plugin becomes visible.

        Execution Thread: **Main or Background**.

        Any UI updates MUST use the `@run_in_loop` decorator.
        """
        self.on_current_frame_changed(self.api.current_frame)

    def on_current_frame_changed(self, n: int) -> None:
        """
        Called when the current playback/scrub frame changes while the plugin is visible.

        Execution Thread: **Main or Background**.

        Any UI updates MUST use the `@run_in_loop` decorator
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
        Called when a context menu in the active video view is requested.

        Forwarded BEFORE the view processes it. Call `event.ignore()` to suppress the view's default context menu
        and display a custom menu.

        Execution Thread: **Main**.
        """

    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        """
        Called when the mouse moves over the active video view.

        Coordinates in `event.position()` are in **viewport coordinates**.
        To map to source image pixel coordinates, use `self.api.current_view.map_to_image(event.position().toPoint())`.

        Execution Thread: **Main**.
        """

    def on_view_mouse_pressed(self, event: QMouseEvent) -> None:
        """
        Called when a mouse button is pressed in the active video view.

        Coordinates in `event.position()` are in **viewport coordinates**.
        To map to source image pixel coordinates, use `self.api.current_view.map_to_image(event.position().toPoint())`.

        Execution Thread: **Main**.
        """

    def on_view_mouse_released(self, event: QMouseEvent) -> None:
        """
        Called when a mouse button is released in the active video view.

        Coordinates in `event.position()` are in **viewport coordinates**.
        To map to source image pixel coordinates, use `self.api.current_view.map_to_image(event.position().toPoint())`.

        Execution Thread: **Main**.
        """

    def on_view_rect_selection_changed(self, rect: QRect) -> None:
        """
        Called when the user drags or alters the rectangular selection overlay.

        Execution Thread: **Main**.

        Args:
            rect: Selection rectangle in **source image pixel coordinates**.
                An empty QRect indicates that the selection was cleared.

        """

    def on_view_rect_selection_finished(self, rect: QRect) -> None:
        """
        Called when rectangular selection interaction completes (mouse released or finished).

        Execution Thread: **Main**.

        Args:
            rect: Final selection rectangle in **source image pixel coordinates**.
                An empty QRect indicates that the selection was cleared.

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
        Called when the plugin widget is hidden, dock tab backgrounded, or pane collapsed.

        Execution Thread: **Main**.
        """


class PluginGraphicsView(BaseGraphicsView):
    """
    Graphics view for plugins designed to display custom VapourSynth output.

    Note:
        Instances parented within a `WidgetPluginBase` hierarchy are automatically discovered
        and managed by the controller. Ensure views are properly parented and set to visible to receive frame updates.
    """

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
        _get_controller(self.api).init_view(self, refresh=True)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the current video output changes.

        Execution Thread: **Main or Background**.

        Any UI updates MUST use the `@run_in_loop` decorator

        Warning:
            - Do not call `self.refresh()` here, as it will cause an infinite loop.
            - If you need to update the display manually, use `self.update_display()`.

        """

    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        """
        Called when the current frame changes.

        Execution Thread: **Main or Background**.

        If you need to update the display manually, use `self.update_display()` or `@run_in_loop`.

        Warning:
            - Do not call `self.refresh()` here, as it will cause an infinite loop.
            - The `f` VideoFrame is closed immediately after this method exits.
              Any `QImage` produced via `frame_to_qimage(f)` MUST be copied (`.copy()`)
              to prevent memory corruption from accessing freed frame memory.

        Args:
            n: Frame number.
            f: Packed VideoFrame in GRAY32 / packed format.
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
    """
    Interface for plugins that intercept and transform raw VapourSynth video or audio output nodes.

    Note:
        Registered via `vsview_get_video_processor` or `vsview_get_audio_processor`.

        Only **one** video processor and **one** audio processor can be active application-wide.
        The first registered plugin takes precedence.
    """

    __plugin_base__ = True

    def prepare(self, node: NodeT, /) -> NodeT:
        """
        Process the input node and return a modified node of the same type.

        Called during output initialization within the VapourSynth environment before
        packing or playback pipeline setup.
        The returned node must preserve clip length and stream compatibility.

        Args:
            node: The raw input node (VideoNode or AudioNode).

        Returns:
            The processed node compatible with the player's output requirements.
        """
        raise NotImplementedError
