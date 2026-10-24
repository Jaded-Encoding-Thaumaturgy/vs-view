from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, MutableMapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, override
from unittest.mock import MagicMock

from jetpytools import to_arr
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QImage, QPixmap, QShortcut
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from vsengine import ManagedEnvironment

from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins._controller import WorkspaceBlocker as WorkspaceBlockerImplementation
from vsview.app.plugins._controller import register_controller
from vsview.app.plugins._interface import _PluginLike, _PluginSettingsStore
from vsview.app.plugins.api import PluginAPI, WidgetPluginBase, WorkspaceBlocker
from vsview.app.plugins.contracts import AudioOutputProxy, VideoOutputProxy
from vsview.app.plugins.exceptions import NoCurrentVideoOutputError
from vsview.app.settings import SettingsManager
from vsview.types import Frame, Time


class MockQObject(QObject):
    """
    Dynamic mock adapter satisfying PySide6 C++ QObject pointer checks.

    Delegates non-Qt attribute lookups, assignments, and method invocations
    to an internal MagicMock while presenting a real QObject identity to PySide6.
    """

    def __init__(self, parent: QObject | None = None, **kwargs: Any) -> None:
        super().__init__(parent)
        object.__setattr__(self, "_mock", MagicMock(**kwargs))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._mock, name)

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            setattr(self._mock, name, value)
            super().__setattr__(name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._mock(*args, **kwargs)


class HarnessTesting[T]:
    def __init__(self, harness: T, allowed_properties: Sequence[str]) -> None:
        self.harness = harness
        self.properties = allowed_properties

    def __getattr__(self, name: str) -> Any:
        assert name in self.properties
        return getattr(self.harness, name)


class HarnessViewportProxy:
    def __init__(self) -> None:
        self._cursor_pos = QPoint(0, 0)
        self._cursor: QCursor | Qt.CursorShape = Qt.CursorShape.OpenHandCursor
        self.testing = HarnessTesting(self, ["_cursor"])

    @property
    def cursor_pos(self) -> QPoint:
        return self._cursor_pos

    def set_cursor(self, cursor: QCursor | Qt.CursorShape) -> None:
        self._cursor = cursor

    def map_from_global(self, point: QPoint) -> QPoint:
        return point


class HarnessGraphicsViewProxy:
    def __init__(self, harness: HarnessPluginController | None = None) -> None:
        self.rect_selection_enabled = False
        self.viewport = HarnessViewportProxy()
        self._rect_selection = QRect()
        self._harness = harness
        self._cursor_pos = QPointF(0.0, 0.0)
        self._image = QImage(64, 64, QImage.Format.Format_RGB888)
        self._image.fill(Qt.GlobalColor.black)
        self._pixmap = QPixmap.fromImage(self._image)

    @property
    def pixmap(self) -> QPixmap:
        return self._pixmap

    @property
    def image(self) -> QImage:
        return self._image

    @property
    def cursor_pos(self) -> QPointF:
        return self._cursor_pos

    @property
    def rect_selection(self) -> QRect:
        """Return the current rectangular selection in source image pixel coordinates."""
        return self._rect_selection

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        if self.rect_selection == rect and not finished:
            return
        self._rect_selection = rect
        if self._harness is not None:
            for plugin in self._harness.managed_plugins:
                plugin.on_view_rect_selection_changed(rect)
                if finished:
                    plugin.on_view_rect_selection_finished(rect)

    def clear_rect_selection(self) -> None:
        self.set_rect_selection(QRect(), finished=True)

    def map_to_scene(self, point: QPoint | QPointF) -> QPointF:
        return QPointF(point)

    def map_to_image(self, point: QPoint | QPointF) -> QPointF:
        return QPointF(point)

    def _set_image(self, image: QImage) -> None:
        self._image = image
        self._pixmap = QPixmap.fromImage(image)


class HarnessTimelineProxy:
    def __init__(self) -> None:
        self.testing = HarnessTesting(self, ["_notches"])
        self._frame_count = 100
        self._mode: Literal["frame", "time"] = "frame"
        self._notches = dict[str, list[tuple[Any, ...]]]()

    @property
    def mode(self) -> Literal["frame", "time"]:
        return self._mode

    def add_notch(
        self,
        identifier: str,
        data: Any,
        color: Any = Qt.GlobalColor.black,
        label: str = "",
        notch_id: Any = None,
        *,
        update: bool = True,
    ) -> None:
        self._notches.setdefault(identifier, []).append((data, color, label, notch_id))

    def discard_notch(self, identifier: str, data: Any, notch_id: Any = None, *, update: bool = True) -> None:
        if identifier in self._notches:
            self._notches[identifier] = [
                n for n in self._notches[identifier] if (notch_id is not None and n[3] != notch_id)
            ]

    def clear_notches(self, identifier: str | Iterable[str], *, update: bool = True) -> None:
        for i in to_arr(identifier):
            self._notches.pop(i, None)

    def update(self) -> None: ...


class _HarnessPlaybackState:
    def __init__(self) -> None:
        self.current_frame = Frame(0)
        self.is_playing = False


class HarnessPlaybackProxy:
    def __init__(self, harness: HarnessPluginController | None = None) -> None:
        self._harness = harness
        self.fps = Fraction(24, 1)
        self.testing = HarnessTesting(self, ["_state"])
        self._state = _HarnessPlaybackState()

    def seek(self, frame_or_time: int | timedelta, /) -> bool:
        if self._harness is None:
            return False
        if isinstance(frame_or_time, timedelta):
            frame = self._harness.current_voutput.time_to_frame(frame_or_time)
        else:
            frame = frame_or_time
        self._harness.set_current_frame(int(frame))
        return True


class HarnessPluginController(QObject):
    """
    Formalized test harness satisfying PluginWorkspaceController for testing plugins
    and dock widgets without requiring a heavyweight LoaderWorkspace.
    """

    statusMessage = Signal(str)
    globalSettingsChanged = Signal()
    localSettingsChanged = Signal(str)
    aboutToSaveGlobal = Signal()
    aboutToSaveLocal = Signal(str)

    def __init__(self, qtbot: QtBot, tmp_path: Path | None = None, env: ManagedEnvironment | None = None) -> None:
        super().__init__()
        self._qtbot = qtbot
        self._tmp_path = tmp_path
        self._file_path: Path | None = (tmp_path / "test_script.vpy") if tmp_path else None
        self.env: ManagedEnvironment | None = env

        self._current_frame = Frame(0)
        self._current_raw_voutput: VideoOutput | None = None
        self._current_voutput: VideoOutputProxy | None = None
        self._voutputs = list[VideoOutputProxy]()
        self._aoutputs = list[AudioOutputProxy]()
        self._current_aoutput: AudioOutputProxy | None = None
        self._busy = False

        self.destroy_callbacks = list[Callable[[], Any]]()
        self.managed_plugins = list[WidgetPluginBase[Any, Any]]()

        self.current_view_proxy = HarnessGraphicsViewProxy(self)
        self.timeline_proxy = HarnessTimelineProxy()
        self.playback_proxy = HarnessPlaybackProxy(self)

        self._settings_store = _PluginSettingsStore(lambda: self._file_path)
        self.api = PluginAPI(self)
        register_controller(self.api, self)  # pyright: ignore[reportArgumentType]

        # Wire signals between controller and API
        self.globalSettingsChanged.connect(self.api.globalSettingsChanged)
        self.localSettingsChanged.connect(self.api.localSettingsChanged)
        self.aboutToSaveGlobal.connect(self.api.aboutToSaveGlobal)
        self.aboutToSaveLocal.connect(self.api.aboutToSaveLocal)
        self.api.statusMessage.connect(self.statusMessage)

        SettingsManager.signals.connect_global_weak(self._on_global_settings_changed)
        SettingsManager.signals.connect_local_weak(self._on_local_settings_changed)
        SettingsManager.signals.connect_about_global_weak(self._on_about_to_save_global)
        SettingsManager.signals.connect_about_local_weak(self._on_about_to_save_local)

    # PluginWorkspaceController properties

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def current_frame(self) -> Frame:
        return self._current_frame

    @property
    def current_time(self) -> Time:
        if self._current_voutput is not None:
            return self._current_voutput.frame_to_time(self._current_frame)
        raise NoCurrentVideoOutputError("No video output is currently available in the test harness.")

    @property
    def current_video_index(self) -> int:
        return 0

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def voutputs(self) -> list[VideoOutputProxy]:
        return self._voutputs

    @property
    def current_voutput(self) -> VideoOutputProxy:
        if self._current_voutput is not None:
            return self._current_voutput
        raise NoCurrentVideoOutputError("No video output is currently available in the test harness.")

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        return self._aoutputs

    @property
    def current_aoutput(self) -> AudioOutputProxy | None:
        return self._current_aoutput

    @property
    def is_playing(self) -> bool:
        return self.playback_proxy._state.is_playing

    @property
    def settings(self) -> SimpleNamespace:
        return self.api.settings

    # PluginWorkspaceController methods
    def blocker(self, caller: Any = None) -> WorkspaceBlocker:
        return self.api.blocker(caller)

    def create_blocker(self, api: PluginAPI, caller: Any = None) -> WorkspaceBlocker:
        return WorkspaceBlockerImplementation(lambda locked: setattr(self, "_busy", locked))

    def init_view(self, view: Any, plugin: Any = None, refresh: bool = False) -> None: ...

    @contextmanager
    def vs_context(self) -> Generator[None]:
        if self.env is None or self.env.disposed:
            raise RuntimeError("HarnessPluginController requires an active, undisposed ManagedEnvironment.")
        with self.env.use():
            yield

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        self.destroy_callbacks.append(cb)

    def get_local_storage(self, plugin: WidgetPluginBase[Any, Any] | _PluginLike) -> Path | None:
        if self._file_path is None:
            return None
        storage = self._file_path.parent / ".vsview_storage" / plugin.identifier
        storage.mkdir(parents=True, exist_ok=True)
        return storage

    def register_action(
        self,
        action_id: str,
        action: QAction,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> None:
        self.api.register_action(action_id, action, context=context)

    def register_shortcut(
        self,
        action_id: str,
        callback: Callable[[], Any],
        parent: QWidget,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> QShortcut:
        return self.api.register_shortcut(action_id, callback, parent, context=context)

    def get_shortcut_label(self, action_id: str) -> str:
        return self.api.get_shortcut_label(action_id)

    # Settings Delegation to Production _PluginSettingsStore
    def get_cached_proxy_settings(self, plugin: _PluginLike, scope: str) -> Any:
        return self._settings_store.get_proxy(plugin, scope)

    def update_settings(self, plugin: _PluginLike, scope: str, **updates: Any) -> None:
        self._settings_store.update(plugin, scope, **updates)
        if scope == "global":
            self.globalSettingsChanged.emit()
        elif self._file_path is not None:
            self.localSettingsChanged.emit(str(self._file_path))

    # Harness Helpers & State Drivers
    def create_plugin[T: WidgetPluginBase[Any, Any]](self, plugin_cls: type[T]) -> T:
        """Instantiate a plugin widget, wire it to the harness API, and register it with qtbot."""
        plugin = plugin_cls(None, self.api)  # pyright: ignore[reportArgumentType]
        self._qtbot.addWidget(plugin)

        self.managed_plugins.append(plugin)
        if self._current_voutput is not None:
            with self.vs_context():
                plugin.on_current_voutput_changed(self._current_voutput, self.current_video_index)
        return plugin

    def set_current_voutput(self, voutput: VideoOutput | VideoOutputProxy, tab_index: int = 0) -> VideoOutputProxy:
        """Update active video output and notify all managed plugins."""
        if isinstance(voutput, VideoOutput):
            self._current_raw_voutput = voutput
            proxy = VideoOutputProxy(
                voutput.vs_index,
                voutput.vs_name,
                voutput.vs_output,
                voutput.props,
                voutput.framedurs,
                voutput.cum_durations,
                voutput.midpoints,
                voutput.kwargs,
                voutput.info,
                voutput.packer,
            )
        elif isinstance(voutput, VideoOutputProxy):
            self._current_raw_voutput = None
            proxy = voutput
        else:
            raise TypeError(f"Expected VideoOutput or VideoOutputProxy, got {type(voutput).__name__}")

        self._current_voutput = proxy
        if proxy not in self._voutputs:
            self._voutputs.append(proxy)
        self.timeline_proxy._frame_count = proxy.vs_output.clip.num_frames
        self.playback_proxy.fps = proxy.vs_output.clip.fps

        self._update_frame_state(0)

        for plugin in self.managed_plugins:
            plugin.on_current_voutput_changed(proxy, tab_index)
        return proxy

    def set_current_aoutput(self, aoutput: AudioOutput | AudioOutputProxy) -> AudioOutputProxy:
        """Update active audio output and track in harness outputs."""
        if isinstance(aoutput, AudioOutput):
            proxy = AudioOutputProxy(aoutput.vs_index, aoutput.vs_name, aoutput.vs_output, aoutput.kwargs)
        elif isinstance(aoutput, AudioOutputProxy):
            proxy = aoutput
        else:
            raise TypeError(f"Expected AudioOutput or AudioOutputProxy, got {type(aoutput).__name__}")

        self._current_aoutput = proxy
        if proxy not in self._aoutputs:
            self._aoutputs.append(proxy)
        return proxy

    def set_current_frame(self, frame: int) -> None:
        """Update active frame and notify all managed plugins."""
        self._current_frame = Frame(frame)
        self.playback_proxy._state.current_frame = self._current_frame
        self._update_frame_state(frame)
        for plugin in self.managed_plugins:
            plugin.on_current_frame_changed(frame)

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        """Update graphics view rect selection and forward to managed plugins."""
        self.current_view_proxy.set_rect_selection(rect, finished=finished)

    def clear_rect_selection(self) -> None:
        """Clear graphics view rect selection and forward to managed plugins."""
        self.current_view_proxy.clear_rect_selection()

    def on_workspace_loaded(self) -> None:
        """Forward on_workspace_loaded lifecycle event to managed plugins."""
        for plugin in self.managed_plugins:
            plugin.on_workspace_loaded()

    def on_workspace_destroy(self) -> None:
        """Forward on_workspace_destroy lifecycle event to managed plugins."""
        for plugin in self.managed_plugins:
            plugin.on_workspace_destroy()

    on_playback_started = MagicMock()
    on_playback_stopped = MagicMock()
    on_current_frame_changed = MagicMock()
    register_plugin_nodes_to_buffer = MagicMock()

    def destroy(self) -> None:
        """Execute all registered destroy callbacks and notify managed plugins."""
        while self.destroy_callbacks:
            cb = self.destroy_callbacks.pop()
            cb()
        self.on_workspace_destroy()

    def _update_frame_state(self, frame: int) -> None:
        if self._current_voutput is None:
            return

        if frame not in self._current_voutput.props:
            if not isinstance(self._current_voutput.props, MutableMapping):
                raise TypeError(
                    f"current_voutput.props must be a MutableMapping to cache frame properties, "
                    f"got {type(self._current_voutput.props).__name__}"
                )
            with self.vs_context():
                f = self._current_voutput.vs_output.clip.get_frame(frame)
                self._current_voutput.props[frame] = dict(f.props)

        if self._current_raw_voutput is not None:
            prepared_clip = getattr(self._current_raw_voutput, "prepared_clip", None)
            if prepared_clip is not None:
                with self.vs_context():
                    pf = prepared_clip.get_frame(frame)
                    qimg = self._current_raw_voutput.packer.frame_to_qimage(pf).copy()
                    self.current_view_proxy._set_image(qimg)

    def _on_global_settings_changed(self) -> None:
        self._settings_store.invalidate("global")
        self._settings_store.invalidate("local")
        self.globalSettingsChanged.emit()

    def _on_local_settings_changed(self, path: str) -> None:
        self._settings_store.invalidate("local")
        self.localSettingsChanged.emit(path)

    def _on_about_to_save_global(self) -> None:
        self.aboutToSaveGlobal.emit()

    def _on_about_to_save_local(self, path: str) -> None:
        self.aboutToSaveLocal.emit(path)
