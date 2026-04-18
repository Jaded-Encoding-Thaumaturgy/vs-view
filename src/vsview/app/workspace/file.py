from __future__ import annotations

from base64 import b64decode, b64encode
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from functools import wraps
from importlib.util import find_spec
from logging import getLogger
from pathlib import Path
from typing import Any, ClassVar, Concatenate, NamedTuple, overload

from jetpytools import cachedproperty, to_arr
from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QWidget
from vapoursynth import VideoNode

from ...api._helpers import output_metadata
from ...assets import IconName
from ...vsenv import run_in_loop
from ..plugins.manager import PluginManager
from ..settings import SettingsManager
from ..settings.models import LocalSettings
from ..views.timeline import Time
from .loader import LoaderWorkspace, VSEngineWorkspace

logger = getLogger(__name__)


@overload
def requires_content[W: GenericFileWorkspace, **P, R](
    func: Callable[Concatenate[W, P], R],
) -> Callable[Concatenate[W, P], R | None]: ...


@overload
def requires_content[W: GenericFileWorkspace, **P, R0, R1](
    *,
    return_fallback: Callable[[], R1],
) -> Callable[[Callable[Concatenate[W, P], R0]], Callable[Concatenate[W, P], R0 | R1]]: ...


def requires_content[W: GenericFileWorkspace, **P, R0, R1](
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
                return func(self, *args, **kwargs)

            if return_fallback:
                logger.debug("Content is not available, returning fallback value")
                return return_fallback()

            return None

        return wrapper

    return decorator if func is None else decorator(func)


class GenericFileWorkspace(LoaderWorkspace[Path]):
    """A workspace for managing and viewing files."""

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
        self.setAcceptDrops(True)

        self._autosave_timer = QTimer(self, timerType=Qt.TimerType.VeryCoarseTimer)
        self._autosave_timer.timeout.connect(self._on_autosave_timer_timeout)

        self.load_btn.clicked.connect(self._on_open_file_button_clicked)
        self.error_load_btn.clicked.connect(self._on_open_file_button_clicked)

        self.tbar.playback_container.settingsChanged.connect(self._on_playback_settings_changed)

        SettingsManager.signals.aboutToSaveLocal.connect(lambda _: self.snapshot_settings())
        SettingsManager.signals.localChanged.connect(lambda _: self._on_local_settings_changed())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._get_supported_drop_file(event) is not None:
            event.acceptProposedAction()
            return

        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._get_supported_drop_file(event) is not None:
            event.acceptProposedAction()
            return

        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if (dropped_file := self._get_supported_drop_file(event)) is None:
            event.ignore()
            return

        self.load_content(dropped_file)
        event.acceptProposedAction()

    def deleteLater(self) -> None:
        self._autosave_timer.stop()
        self.playback.stop()

        if hasattr(self, "content"):
            SettingsManager.save_local(self.content, self.local_settings)

        return super().deleteLater()

    @property
    @requires_content(return_fallback=lambda: SettingsManager.default_local_settings)
    def local_settings(self) -> LocalSettings:
        """Return the local settings for this workspace."""
        return SettingsManager.get_local_settings(self.content)

    @cachedproperty
    def supported_suffixes(self) -> frozenset[str]:
        return frozenset(
            suffix.lower() for file_filter in self.filters for suffix in to_arr(file_filter.suffix) if suffix != "*"
        )

    @requires_content
    def snapshot_settings(self) -> None:
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

        # Save layout state
        self.local_settings.layout.plugin_splitter_sizes = self.plugin_splitter.sizes()
        self.local_settings.layout.plugin_tab_index = self.plugin_splitter.plugin_tabs.currentIndex()
        self.local_settings.layout.dock_state = b64encode(self.dock_container.saveState().data()).decode("ascii")

    def init_load(self, frame: int | None = None, time: float | None = None, tab_index: int | None = None) -> None:
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
            self.playback.state.current_time = Time(self.local_settings.last_time.total_seconds())

        if tab_index is None:
            self.outputs_manager.current_video_index = self.local_settings.last_output_tab_index

        PluginManager.populate_default_settings("local", self.content)

    @requires_content(return_fallback=dict[int, Any])
    def get_output_metadata(self) -> dict[int, Any]:
        return output_metadata.get(str(self.content), {})

    def load_content(
        self,
        content: Path,
        /,
        frame: int | None = None,
        time: float | None = None,
        tab_index: int | None = None,
    ) -> Future[None]:
        with self._restart_autosave():
            return super().load_content(content, frame, time, tab_index)

    def reload_content(self) -> Future[None]:
        with self._restart_autosave():
            return super().reload_content()

    @run_in_loop(return_future=False)
    def clear_failed_load(self) -> None:
        self._autosave_timer.stop()
        super().clear_failed_load()

    @run_in_loop(return_future=False)
    def load_plugins(self) -> None:
        if not self.plugins_loaded:
            super().load_plugins()
            self._restore_layout()

    @contextmanager
    def _restart_autosave(self) -> Iterator[None]:
        @run_in_loop(return_future=False)
        def stop_timer() -> int:
            remaining_time = self._autosave_timer.remainingTime()
            self._autosave_timer.stop()
            return remaining_time

        remaining_time = stop_timer()

        yield

        @run_in_loop(return_future=False)
        def restart_timer() -> None:
            self._autosave_timer.start(
                remaining_time
                if remaining_time > 0
                else (self.global_settings.autosave.minute * 60 + self.global_settings.autosave.second) * 1000
            )

        restart_timer()

    def _restore_layout(self) -> None:
        layout = self.local_settings.layout

        if layout.plugin_splitter_sizes:
            self.plugin_splitter.setSizes(layout.plugin_splitter_sizes)

        if layout.plugin_tab_index is not None:
            self.plugin_splitter.plugin_tabs.setCurrentIndex(layout.plugin_tab_index)

        if layout.dock_state:
            self.dock_container.restoreState(QByteArray(b64decode(layout.dock_state)))

        self.dock_toggle_btn.setChecked(any(not dock.isHidden() for dock in self.docks))

    def _get_supported_drop_file(self, event: QDropEvent) -> Path | None:
        if (mime_data := event.mimeData()).hasUrls():
            for url in mime_data.urls():
                file_path = Path(url.toLocalFile())
                if file_path.is_file() and file_path.suffix.lower().removeprefix(".") in self.supported_suffixes:
                    return file_path

        return None

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

    def _on_playback_settings_changed(self, seek_step: int, speed: float, uncapped: bool) -> None:
        self.local_settings.playback.seek_step = seek_step
        self.local_settings.playback.speed = speed
        self.local_settings.playback.uncapped = uncapped

    @requires_content
    def _on_local_settings_changed(self) -> None:
        self.tab_manager.sync_playhead_btn.set_state(state=self.local_settings.synchronization.sync_playhead)
        self.tab_manager.sync_zoom_btn.setChecked(self.local_settings.synchronization.sync_zoom)
        self.tab_manager.sync_scroll_btn.setChecked(self.local_settings.synchronization.sync_scroll)
        self.tab_manager.autofit_btn.setChecked(self.local_settings.synchronization.autofit_all_views)

    @requires_content
    def _on_autosave_timer_timeout(self) -> None:
        SettingsManager.save_local(self.content, self.local_settings)


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

    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        try:
            with self.env.use():
                if not hasattr(self.env.core, "bs"):
                    raise RuntimeError("The BestSource plugin 'bs' is required to load a file")

                self._source().set_output()
        except Exception:
            logger.exception("There was an error:")
            raise

        logger.debug("Loaded file: %s", self.content)

    def _source(self) -> VideoNode:
        if find_spec("vssource"):
            from vssource import BestSource

            try:
                return BestSource(show_pretty_progress=True).source(self.content, 0)
            except Exception as e:
                logger.warning("vssource.BestSource failed to index with the error %r", str(e))

        logger.info("Using fallback bs.VideoSource...")
        return self.env.core.bs.VideoSource(str(self.content))


class PythonScriptWorkspace(GenericFileWorkspace, VSEngineWorkspace[Path]):
    title = "Script"
    icon = IconName.FILE_TEXT
    caption = "Open VapourSynth Script"
    filters = (GenericFileWorkspace.FileFilter("Python & VapourSynth Files", ["py", "vpy"]),)

    content_type = "script"

    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        return super().loader()
