from __future__ import annotations

from collections.abc import Callable, Generator, MutableMapping
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from vsengine import ManagedEnvironment

from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.api import WidgetPluginBase, WorkspaceBlocker
from vsview.app.plugins.contracts import AudioOutputProxy, VideoOutputProxy
from vsview.app.plugins.exceptions import NoCurrentVideoOutputError
from vsview.app.settings import SettingsManager, ShortcutManager
from vsview.types import Frame, Time


class HarnessViewportProxy:
    """Lightweight viewport proxy."""

    def __init__(self) -> None:
        self.cursor_pos = QPoint(0, 0)
        self.cursor: QCursor | Qt.CursorShape = Qt.CursorShape.OpenHandCursor

    def set_cursor(self, cursor: QCursor | Qt.CursorShape) -> None:
        self.cursor = cursor


class HarnessPixmapItemProxy:
    """Lightweight pixmap item proxy."""

    def __init__(self, get_pixmap: Callable[[], QPixmap]) -> None:
        self._get_pixmap = get_pixmap

    def pixmap(self) -> QPixmap:
        return self._get_pixmap()


class HarnessGraphicsViewProxy:
    """Lightweight graphics view proxy providing real geometry and viewport states."""

    def __init__(self, harness: PluginTestHarness | None = None) -> None:
        self._harness = harness
        self.rect_selection = QRect()
        self.rect_selection_enabled = False
        self.cursor_pos = QPointF(0.0, 0.0)
        self.viewport = HarnessViewportProxy()
        self._image = QImage(64, 64, QImage.Format.Format_RGB888)
        self._image.fill(Qt.GlobalColor.black)
        self._pixmap = QPixmap.fromImage(self._image)
        self.pixmap_item = HarnessPixmapItemProxy(lambda: self._pixmap)
        self._in_set_rect = False

    @property
    def pixmap(self) -> QPixmap:
        return self._pixmap

    @property
    def image(self) -> QImage:
        return self._image

    def set_image(self, image: QImage) -> None:
        self._image = image
        self._pixmap = QPixmap.fromImage(image)

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        if self.rect_selection == rect and not finished:
            return
        self.rect_selection = rect
        if self._harness is not None:
            for plugin in self._harness.managed_plugins:
                plugin.on_view_rect_selection_changed(rect)
                if finished:
                    plugin.on_view_rect_selection_finished(rect)

    def clear_rect_selection(self) -> None:
        self.set_rect_selection(QRect(), finished=True)

    def map_to_scene(self, point: QPoint | QPointF) -> QPointF:
        return QPointF(float(point.x()), float(point.y()))

    def map_to_image(self, point: QPoint | QPointF) -> QPointF:
        return QPointF(float(point.x()), float(point.y()))


class HarnessTimelineProxy:
    """Lightweight timeline proxy providing frame mapping."""

    def __init__(self) -> None:
        self.frame_count = 100
        self.current_frame = Frame(0)
        self.fps = Fraction(24, 1)

    @property
    def timecode(self) -> str:
        return "00:00:00.000"


class HarnessPlaybackState:
    """Lightweight playback state proxy."""

    def __init__(self) -> None:
        self.current_frame = Frame(0)
        self.is_playing = False


class HarnessPlaybackProxy:
    """Lightweight playback state proxy."""

    def __init__(self) -> None:
        self.is_playing = False
        self.fps = Fraction(24, 1)
        self.state = HarnessPlaybackState()


class HarnessBlocker(QObject):
    """Real non-mock blocker managing workspace busy state."""

    def __init__(self, harness: PluginTestHarness) -> None:
        super().__init__()
        self._harness = harness
        self.locked = False

    def __enter__(self) -> None:
        self.acquire(block=True)

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()

    def acquire(self, block: bool = True, timeout: float | None = None) -> bool:
        self.locked = True
        self._harness.busy = True
        return True

    def release(self) -> None:
        self.locked = False
        self._harness.busy = False


class PluginTestHarness(QObject):
    """
    Formalized test harness satisfying the PluginAPI protocol for testing plugins
    and dock widgets without requiring a heavyweight LoaderWorkspace.
    """

    statusMessage = Signal(str)
    globalSettingsChanged = Signal()
    localSettingsChanged = Signal(str)
    aboutToSaveGlobal = Signal()
    aboutToSaveLocal = Signal(str)

    def __init__(self, qtbot: QtBot, tmp_path: Path | None = None, env: ManagedEnvironment | None = None) -> None:
        super().__init__()
        self.qtbot = qtbot
        self.tmp_path = tmp_path
        self.file_path: Path | None = (tmp_path / "test_script.vpy") if tmp_path else None

        self._current_frame = Frame(0)
        self._current_raw_voutput: VideoOutput | None = None
        self._current_voutput: VideoOutputProxy | None = None
        self._voutputs = list[VideoOutputProxy]()
        self._aoutputs = list[AudioOutputProxy]()
        self._current_aoutput: AudioOutputProxy | None = None
        self._is_playing = False
        self.busy = False

        self.destroy_callbacks = list[Callable[[], Any]]()
        self.managed_plugins = list[WidgetPluginBase[Any, Any]]()

        self.current_view = HarnessGraphicsViewProxy(self)
        self.timeline = HarnessTimelineProxy()
        self.playback = HarnessPlaybackProxy()
        self.env: ManagedEnvironment | None = env

        SettingsManager.signals.connect_global_weak(self._on_global_settings_changed)
        SettingsManager.signals.connect_local_weak(self._on_local_settings_changed)

    def _on_global_settings_changed(self) -> None:
        self.globalSettingsChanged.emit()

    def _on_local_settings_changed(self, path: str) -> None:
        self.localSettingsChanged.emit(path)

    # PluginAPI Protocol Properties
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
    def current_voutput(self) -> VideoOutputProxy:
        if self._current_voutput is not None:
            return self._current_voutput
        raise NoCurrentVideoOutputError("No video output is currently available in the test harness.")

    @property
    def voutputs(self) -> list[VideoOutputProxy]:
        return self._voutputs

    @property
    def aoutputs(self) -> list[AudioOutputProxy]:
        return self._aoutputs

    @property
    def current_aoutput(self) -> AudioOutputProxy | None:
        return self._current_aoutput

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    # PluginAPI Protocol Methods
    def blocker(self, caller: Any = None) -> WorkspaceBlocker:
        return HarnessBlocker(self)  # type: ignore[return-value]

    @contextmanager
    def vs_context(self) -> Generator[None, None, None]:
        if self.env is None or self.env.disposed:
            raise RuntimeError("PluginTestHarness requires an active, undisposed ManagedEnvironment.")
        with self.env.use():
            yield

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        self.destroy_callbacks.append(cb)

    def get_local_storage(self, plugin: WidgetPluginBase[Any, Any]) -> Path | None:
        if self.file_path is None:
            return None
        storage = self.file_path.parent / ".vsview_storage" / plugin.identifier
        storage.mkdir(parents=True, exist_ok=True)
        return storage

    def register_action(
        self,
        action_id: str,
        action: QAction,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> None:
        ShortcutManager.register_action(action_id, action, context=context)

    def register_shortcut(
        self,
        action_id: str,
        callback: Callable[[], Any],
        parent: QWidget,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> QShortcut:
        return ShortcutManager.register_shortcut(action_id, callback, parent, context=context)

    def get_shortcut_label(self, action_id: str) -> str:
        key = ShortcutManager.get_key(action_id)
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText) if key else ""

    # --- Settings Proxy Integration ---

    def _get_cached_proxy_settings(
        self, plugin: WidgetPluginBase[Any, Any], scope: Literal["global", "local"]
    ) -> BaseModel | None:
        if scope not in ("global", "local"):
            raise ValueError(f"Invalid settings scope {scope!r}, must be 'global' or 'local'.")
        model_cls: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model", None)
        if model_cls is None:
            return None

        if scope == "global":
            raw = SettingsManager.global_settings.plugins.get(plugin.identifier, {})
        elif self.file_path is not None:
            raw = SettingsManager.get_local_settings(self.file_path).plugins.get(plugin.identifier, {})
        else:
            raw = {}

        raw_dict = raw if isinstance(raw, dict) else raw.model_dump()
        return model_cls.model_validate(raw_dict)

    def _update_settings(
        self, plugin: WidgetPluginBase[Any, Any], scope: Literal["global", "local"], **updates: Any
    ) -> None:
        if scope not in ("global", "local"):
            raise ValueError(f"Invalid settings scope {scope!r}, must be 'global' or 'local'.")
        model_cls: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model", None)
        if model_cls is None:
            raise ValueError(f"Plugin {plugin.identifier!r} has no {scope}_settings_model defined.")

        current = self._get_cached_proxy_settings(plugin, scope) or model_cls()
        for k, v in updates.items():
            setattr(current, k, v)

        if scope == "global":
            SettingsManager.global_settings.plugins[plugin.identifier] = current
            self.globalSettingsChanged.emit()
        elif self.file_path is not None:
            SettingsManager.get_local_settings(self.file_path).plugins[plugin.identifier] = current
            self.localSettingsChanged.emit(str(self.file_path))

    # Harness Test Helpers
    def create_plugin[T: WidgetPluginBase[Any, Any]](self, plugin_cls: type[T], parent: QWidget | None = None) -> T:
        """Instantiate a plugin widget, wire it to the harness, and register it with qtbot."""
        if parent is not None:
            plugin = plugin_cls(parent, self)  # pyright: ignore[reportArgumentType]
        else:
            plugin = plugin_cls(None, self)  # pyright: ignore[reportArgumentType]
            self.qtbot.addWidget(plugin)

        self.managed_plugins.append(plugin)
        if self._current_voutput is not None:
            if not self.env or self.env.disposed:
                raise RuntimeError("This plugin harness doesn't have any environment or it's already disposed")
            with self.env.use():
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
        self.timeline.frame_count = proxy.vs_output.clip.num_frames
        self.playback.fps = proxy.vs_output.clip.fps

        if 0 not in proxy.props:
            if not isinstance(proxy.props, MutableMapping):
                raise TypeError(
                    f"proxy.props must be a MutableMapping to cache frame properties, got {type(proxy.props).__name__}"
                )
            with self.vs_context():
                f = proxy.vs_output.clip.get_frame(0)
                proxy.props[0] = dict(f.props)

        if self._current_raw_voutput is not None:
            prepared_clip = getattr(self._current_raw_voutput, "prepared_clip", None)
            if prepared_clip is not None:
                with self.vs_context():
                    pf = prepared_clip.get_frame(0)
                    qimg = self._current_raw_voutput.packer.frame_to_qimage(pf).copy()
                    self.current_view.set_image(qimg)

        for plugin in self.managed_plugins:
            plugin.on_current_voutput_changed(proxy, tab_index)
        return proxy

    def set_current_frame(self, frame: int) -> None:
        """Update active frame and notify all managed plugins."""
        self._current_frame = Frame(frame)
        self.playback.state.current_frame = self._current_frame
        if self._current_voutput is not None:
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
                        self.current_view.set_image(qimg)
        for plugin in self.managed_plugins:
            plugin.on_current_frame_changed(frame)

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        """Update graphics view rect selection and forward to managed plugins."""
        self.current_view.set_rect_selection(rect, finished=finished)

    def destroy(self) -> None:
        """Execute all registered destroy callbacks and notify managed plugins."""
        for cb in self.destroy_callbacks:
            cb()
        for plugin in self.managed_plugins:
            plugin.on_workspace_destroy()
