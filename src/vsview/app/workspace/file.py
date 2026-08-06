from __future__ import annotations

import os
from base64 import b64decode, b64encode
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import suppress
from functools import wraps
from importlib.util import find_spec
from logging import DEBUG, getLogger
from pathlib import Path
from typing import Any, ClassVar, Concatenate, NamedTuple, overload, override

from jetpytools import cachedproperty, to_arr
from PySide6.QtCore import QByteArray, QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QWidget
from vapoursynth import VideoNode
from vsengine.futures import UnifiedFuture

from ...api._helpers import output_metadata
from ...assets import IconName
from ...types import Time
from ...vsenv import run_in_loop
from ..settings import SettingsManager
from ..settings.models import LocalSettings
from .loader import LoaderWorkspace, VSEngineWorkspace

logger = getLogger(__name__)


@overload
def requires_content[W: BaseGenericFileWorkspace[Any], **P, R](
    func: Callable[Concatenate[W, P], R],
) -> Callable[Concatenate[W, P], R | None]: ...


@overload
def requires_content[W: BaseGenericFileWorkspace[Any], **P, R0, R1](
    *,
    return_fallback: Callable[[], R1],
) -> Callable[[Callable[Concatenate[W, P], R0]], Callable[Concatenate[W, P], R0 | R1]]: ...


def requires_content[W: BaseGenericFileWorkspace[Any], **P, R0, R1](
    func: Callable[Concatenate[W, P], R0] | None = None,
    return_fallback: Callable[[], R1 | None] | None = None,
) -> (
    Callable[Concatenate[W, P], R0 | None]
    | Callable[[Callable[Concatenate[W, P], R0]], Callable[Concatenate[W, P], R0 | R1]]
):
    """Decorator that checks if the 'content' attribute exists before executing the method."""

    def decorator(func: Callable[Concatenate[W, P], Any]) -> Callable[Concatenate[W, P], Any]:
        @wraps(func)
        def wrapper(self: W, *args: P.args, **kwargs: P.kwargs) -> Any:
            if hasattr(self, "content"):
                logger.log(DEBUG - 1, "Content is available for %s", func)
                return func(self, *args, **kwargs)

            if return_fallback:
                logger.log(DEBUG - 1, "Content is not available for %s, returning fallback value", func)
                return return_fallback()

            return None

        return wrapper

    return decorator if func is None else decorator(func)


class BaseGenericFileWorkspace[PathLike: os.PathLike[str]](LoaderWorkspace[PathLike]):
    """A base workspace for managing and viewing files."""

    class FileFilter(NamedTuple):
        """Named tuple representing a file filter for dialogs."""

        label: str
        """The display label for the filter."""
        suffix: str | Sequence[str]
        """The file extension suffix."""

    caption: ClassVar[str]
    filters: ClassVar[Sequence[FileFilter]]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._autosave_timer = QTimer(self, timerType=Qt.TimerType.VeryCoarseTimer)
        self._autosave_timer.timeout.connect(self._on_autosave_timer_timeout)

        self.tbar.playback_container.settingsChanged.connect(self._on_playback_settings_changed)

        SettingsManager.signals.connect_about_local_weak(self.snapshot_settings)
        SettingsManager.signals.connect_local_weak(self._on_local_settings_changed)

    @override
    def deleteLater(self) -> None:
        self._autosave_timer.stop()
        self.playback.stop()

        if hasattr(self, "content"):
            if (path := self.current_file_path) is not None:
                SettingsManager.save_local(path, self.local_settings)
            del self.content

        return super().deleteLater()

    @property
    def current_file_path(self) -> PathLike | None:
        """Return the current file path for local settings persistence."""
        return getattr(self, "content", None)

    @property
    @requires_content(return_fallback=lambda: SettingsManager.default_local_settings)
    def local_settings(self) -> LocalSettings:
        """Return the local settings for this workspace."""
        return (
            SettingsManager.get_local_settings(path)
            if (path := self.current_file_path) is not None
            else SettingsManager.default_local_settings
        )

    @cachedproperty
    def supported_suffixes(self) -> frozenset[str]:
        return frozenset(
            suffix.lower() for file_filter in self.filters for suffix in to_arr(file_filter.suffix) if suffix != "*"
        )

    @requires_content
    def snapshot_settings(self, _: Any) -> None:
        self.local_settings.last_frame = self.playback.state.current_frame
        self.local_settings.last_time = self.playback.state.current_time
        self.local_settings.last_output_tab_index = self.tab_manager.tabs.currentIndex()
        self.local_settings.synchronization.sync_playhead = self.tab_manager.sync_playhead_state
        self.local_settings.synchronization.sync_zoom = self.tab_manager.is_sync_zoom_enabled
        self.local_settings.synchronization.sync_scroll = self.tab_manager.is_sync_scroll_enabled
        self.local_settings.synchronization.autofit_all_views = self.tab_manager.autofit_btn.isChecked()

        self.local_settings.playback.seek_step = self.tbar.playback_container.settings.seek_step
        self.local_settings.playback.speed = self.tbar.playback_container.settings.speed
        self.local_settings.playback.uncapped = self.tbar.playback_container.settings.uncapped
        self.local_settings.playback.zone_frames = self.tbar.playback_container.settings.zone_frames
        self.local_settings.playback.loop = self.tbar.playback_container.settings.loop
        self.local_settings.playback.step = self.tbar.playback_container.settings.step

        self.local_settings.playback.last_audio_index = self.outputs_manager.current_audio_index
        self.local_settings.playback.current_volume = self.tbar.playback_container.raw_volume
        self.local_settings.playback.muted = self.tbar.playback_container.is_muted
        self.local_settings.playback.audio_delay = self.tbar.playback_container.audio_delay

        self.local_settings.timeline.mode = self.tbar.timeline.mode

        # Save view state
        with suppress(TypeError):
            view = self.tab_manager.current_view
            self.local_settings.view.last_zoom = view.current_zoom
            self.local_settings.view.last_center = view.mapToScene(view.viewport().rect().center())

            # Save layout state
            self.local_settings.layout.plugin_splitter_sizes = self.plugin_splitter.sizes()
            self.local_settings.layout.plugin_tab_index = self.plugin_splitter.plugin_tabs.currentIndex()
            self.local_settings.layout.dock_state = b64encode(self.dock_container.saveState().data()).decode("ascii")

    @override
    def init_load(self, frame: int | None = None, time: float | None = None, tab_index: int | None = None) -> None:
        from ..plugins.manager import PluginManager

        self.tab_manager.sync_playhead_btn.set_state(state=self.local_settings.synchronization.sync_playhead)
        self.tab_manager.sync_zoom_btn.setChecked(self.local_settings.synchronization.sync_zoom)
        self.tab_manager.sync_scroll_btn.setChecked(self.local_settings.synchronization.sync_scroll)
        self.tab_manager.autofit_btn.setChecked(self.local_settings.synchronization.autofit_all_views)

        self.tbar.playback_container.settings.seek_step = self.local_settings.playback.seek_step
        self.tbar.playback_container.settings.speed = self.local_settings.playback.speed
        self.tbar.playback_container.settings.uncapped = self.local_settings.playback.uncapped
        self.tbar.playback_container.settings.zone_frames = self.local_settings.playback.zone_frames
        self.tbar.playback_container.settings.loop = self.local_settings.playback.loop
        self.tbar.playback_container.settings.step = self.local_settings.playback.step

        self.tbar.timeline.mode = self.local_settings.timeline.mode

        self.tbar.playback_container.volume = self.local_settings.playback.current_volume
        self.tbar.playback_container.is_muted = self.local_settings.playback.muted
        self.tbar.playback_container.audio_delay = self.local_settings.playback.audio_delay

        self.outputs_manager.current_audio_index = self.local_settings.playback.last_audio_index

        if frame is None:
            self.playback.state.current_frame = self.local_settings.last_frame
        if time is None:
            self.playback.state.current_time = Time(seconds=self.local_settings.last_time.total_seconds())

        if tab_index is None:
            self.outputs_manager.current_video_index = self.local_settings.last_output_tab_index

        if (path := self.current_file_path) is not None:
            PluginManager.populate_default_settings("local", path)

    @requires_content(return_fallback=dict[int, Any])
    @override
    def get_output_metadata(self) -> dict[int, Any]:
        return output_metadata.get(str(self.content), {})

    @override
    def load_content(
        self,
        content: PathLike,
        /,
        frame: int | None = None,
        time: float | None = None,
        tab_index: int | None = None,
    ) -> UnifiedFuture[int]:
        remaining_time = self._stop_autosave()
        return (
            super()
            .load_content(content, frame, time, tab_index)
            .add_loop_callback(self._restore_zoom_center)
            .add_loop_callback(lambda f: self._start_autosave(f, remaining_time))
        )

    @override
    def reload_content(self) -> UnifiedFuture[int]:
        remaining_time = self._stop_autosave()
        return super().reload_content().add_loop_callback(lambda f: self._start_autosave(f, remaining_time))

    @run_in_loop(return_future=False)
    @override
    def clear_failed_load(self) -> None:
        self._autosave_timer.stop()
        super().clear_failed_load()

    @run_in_loop(return_future=False)
    @override
    def load_plugins(self) -> None:
        if not self.plugins_loaded:
            super().load_plugins()
            self._restore_layout()

    def _start_autosave(self, f: Future[int], remaining_time: int) -> None:
        if f.exception() or f.result() == 1:
            return

        autosave = self.global_settings.autosave
        full_interval = (autosave.minute * 60 + autosave.second) * 1000

        if full_interval <= 0:
            return

        self._autosave_timer.start(remaining_time if remaining_time > 0 else full_interval)

    @run_in_loop(return_future=False)
    def _stop_autosave(self) -> int:
        remaining_time = self._autosave_timer.remainingTime()
        self._autosave_timer.stop()
        return remaining_time

    def _restore_layout(self) -> None:
        layout = self.local_settings.layout

        with QSignalBlocker(self.plugin_splitter), QSignalBlocker(self.plugin_splitter.plugin_tabs):
            if layout.plugin_splitter_sizes:
                self.plugin_splitter.setSizes(layout.plugin_splitter_sizes)

            if layout.plugin_tab_index is not None:
                self.plugin_splitter.plugin_tabs.setCurrentIndex(layout.plugin_tab_index)

        # Sync toolpanel button state manually since signals were blocked
        self._sync_toolpanel_btn(self.plugin_splitter.is_right_panel_visible)

        if layout.dock_state:
            self.dock_container.restoreState(QByteArray(b64decode(layout.dock_state)))

        dock_visible = any(not dock.isHidden() for dock in self.docks)
        self.dock_toggle_btn.setChecked(dock_visible)

    def _restore_zoom_center(self, f: Future[int]) -> None:
        if f.exception() or f.result() > 0:
            return

        v = self.tab_manager.current_view
        if (z := self.local_settings.view.last_zoom) and z != 1.0:
            with QSignalBlocker(v):
                v.slider.setValue(v.zoom_to_slider(z))

        if c := self.local_settings.view.last_center:
            v.update_center(c.toTuple())

    def _on_playback_settings_changed(self, seek_step: int, speed: float, uncapped: bool) -> None:
        self.local_settings.playback.seek_step = seek_step
        self.local_settings.playback.speed = speed
        self.local_settings.playback.uncapped = uncapped

    @requires_content
    def _on_local_settings_changed(self, _: Any) -> None:
        self.tab_manager.sync_playhead_btn.set_state(state=self.local_settings.synchronization.sync_playhead)
        self.tab_manager.sync_zoom_btn.setChecked(self.local_settings.synchronization.sync_zoom)
        self.tab_manager.sync_scroll_btn.setChecked(self.local_settings.synchronization.sync_scroll)
        self.tab_manager.autofit_btn.setChecked(self.local_settings.synchronization.autofit_all_views)

    @requires_content
    def _on_autosave_timer_timeout(self) -> None:
        if (path := self.current_file_path) is not None:
            SettingsManager.save_local(path, self.local_settings)

        # Reset the timer to the full interval if it was running with a temporary remaining_time
        autosave = self.global_settings.autosave
        full_interval = (autosave.minute * 60 + autosave.second) * 1000
        if full_interval > 0:
            if self._autosave_timer.interval() != full_interval:
                self._autosave_timer.start(full_interval)
        else:
            self._autosave_timer.stop()


class GenericFileWorkspace(BaseGenericFileWorkspace[Path]):
    """A workspace for managing and viewing files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self.load_btn.clicked.connect(self._on_open_file_button_clicked)
        self.error_load_btn.clicked.connect(self._on_open_file_button_clicked)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._get_supported_drop_file(event) is not None:
            event.acceptProposedAction()
            return

        event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._get_supported_drop_file(event) is not None:
            event.acceptProposedAction()
            return

        event.ignore()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        if (dropped_file := self._get_supported_drop_file(event)) is None:
            event.ignore()
            return

        self.load_content(dropped_file)
        event.acceptProposedAction()

    def _get_supported_drop_file(self, event: QDropEvent) -> Path | None:
        if (mime_data := event.mimeData()).hasUrls():
            for url in mime_data.urls():
                file_path = Path(url.toLocalFile())
                if file_path.is_file() and file_path.suffix.lower().removeprefix(".") in self.supported_suffixes:
                    return file_path

        return None

    @Slot()
    def _on_open_file_button_clicked(self) -> None:
        file_path_str, _ = QFileDialog.getOpenFileName(
            self,
            self.caption,
            filter=";;".join(
                f"{f.label} (*.{' *.'.join(to_arr(f.suffix))})"
                for f in [*self.filters, self.FileFilter("All Files", "*")]
            ),
        )

        if not file_path_str:
            logger.info("No file selected")
            return

        self.load_content(Path(file_path_str))


class VideoFileWorkspace(GenericFileWorkspace):
    title = "File"
    icon = IconName.FILE_VIDEO
    caption = "Open Video File"
    filters = (
        GenericFileWorkspace.FileFilter(
            "Video & Image Files",
            [
                "mp4",
                "avi",
                "mkv",
                "mov",
                "webm",
                "flv",
                "wmv",
                "m2ts",
                "ts",
                "png",
                "jpg",
                "jpeg",
                "gif",
                "bmp",
                "tiff",
                "webp",
                "ico",
            ],
        ),
    )

    @override
    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        try:
            with self.env.use():
                self._source().set_output()
        except Exception:
            logger.exception("There was an error:")
            raise

        logger.debug("Loaded file: %s", self.content)

    def _source(self) -> VideoNode:
        if find_spec("vssource"):
            from vssource import BestSource

            from ...logging import console

            pr = BestSource.get_progress(console=console)
            task = pr.add_task("Indexing with BestSource...", total=100.0, visible=False)
            bs = BestSource(show_pretty_progress=lambda pct: pr.update(task, completed=pct, visible=True))
            return bs.source(self.content, 0)

        logger.debug("Using fallback bs.VideoSource...")
        return self.env.core.bs.VideoSource(str(self.content))


class PythonScriptWorkspace(GenericFileWorkspace, VSEngineWorkspace[Path]):
    title = "Script"
    icon = IconName.FILE_TEXT
    caption = "Open VapourSynth Script"
    filters = (GenericFileWorkspace.FileFilter("Python & VapourSynth Files", ["py", "vpy"]),)

    content_type = "script"

    @override
    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        return super().loader()
