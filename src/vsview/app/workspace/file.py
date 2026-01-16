from collections.abc import Sequence
from importlib.util import find_spec
from logging import getLogger
from pathlib import Path
from typing import ClassVar, NamedTuple

from jetpytools import to_arr
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QWidget
from vsengine.loops import get_loop

from ...api._helpers import output_metadata
from ...assets import IconName
from ...vsenv import run_in_background, run_in_loop
from ..plugins.manager import PluginManager
from ..settings import SettingsManager
from ..settings.models import LocalSettings
from .loader import LoaderWorkspace, VSEngineWorkspace

logger = getLogger(__name__)


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

        self._autosave_timer = QTimer(self, timerType=Qt.TimerType.VeryCoarseTimer)
        self._autosave_timer.timeout.connect(self.save_settings)

        self.load_btn.clicked.connect(self._on_open_file_button_clicked)
        self.error_load_btn.clicked.connect(self._on_open_file_button_clicked)

        self.tab_manager.sync_playhead_btn.toggled.connect(
            lambda checked: setattr(self.local_settings.synchronization, "sync_playhead", checked)
        )
        self.tab_manager.sync_zoom_btn.toggled.connect(
            lambda checked: setattr(self.local_settings.synchronization, "sync_zoom", checked)
        )
        self.tab_manager.sync_scroll_btn.toggled.connect(
            lambda checked: setattr(self.local_settings.synchronization, "sync_scroll", checked)
        )
        self.tab_manager.autofit_btn.toggled.connect(
            lambda checked: setattr(self.local_settings.synchronization, "autofit_all_views", checked)
        )
        SettingsManager.signals.localChanged.connect(self._on_local_settings_changed)

    def deleteLater(self) -> None:
        self._autosave_timer.stop()
        self._stop_playback()

        if hasattr(self, "content"):
            self.save_settings().result()

        return super().deleteLater()

    @property
    def local_settings(self) -> LocalSettings:
        """Return the local settings for this workspace."""
        return SettingsManager.get_local_settings(self.content)

    @run_in_background(name="SaveSettings")
    def save_settings(self) -> None:
        self.local_settings.last_frame = self.current_frame
        self.local_settings.last_output_tab_index = self.tab_manager.tabs.currentIndex()
        self.local_settings.synchronization.sync_playhead = self.tab_manager.is_sync_playhead_enabled
        self.local_settings.synchronization.sync_zoom = self.tab_manager.is_sync_zoom_enabled
        self.local_settings.synchronization.sync_scroll = self.tab_manager.is_sync_scroll_enabled
        self.local_settings.synchronization.autofit_all_views = self.tab_manager.autofit_btn.isChecked()

        self.local_settings.playback.seek_step = self.tbar.playback_container.settings.seek_step
        self.local_settings.playback.speed = self.tbar.playback_container.settings.speed
        self.local_settings.playback.uncapped = self.tbar.playback_container.settings.uncapped
        self.local_settings.playback.zone_frames = self.tbar.playback_container.settings.zone_frames
        self.local_settings.playback.loop = self.tbar.playback_container.settings.loop

        SettingsManager.save_local(self.content, self.local_settings)

    def init_load(self, frame: int | None = None, tab_index: int | None = None) -> None:
        # init tabs button
        self.tab_manager.sync_playhead_btn.setChecked(self.local_settings.synchronization.sync_playhead)
        self.tab_manager.sync_zoom_btn.setChecked(self.local_settings.synchronization.sync_zoom)
        self.tab_manager.sync_scroll_btn.setChecked(self.local_settings.synchronization.sync_scroll)
        self.tab_manager.autofit_btn.setChecked(self.local_settings.synchronization.autofit_all_views)

        self.tbar.playback_container.settings.seek_step = self.local_settings.playback.seek_step
        self.tbar.playback_container.settings.speed = self.local_settings.playback.speed
        self.tbar.playback_container.settings.uncapped = self.local_settings.playback.uncapped
        self.tbar.playback_container.settings.zone_frames = self.local_settings.playback.zone_frames
        self.tbar.playback_container.settings.loop = self.local_settings.playback.loop

        if frame is None:
            self.current_frame = self.local_settings.last_frame

        if tab_index is None:
            self.current_tab_index = self.local_settings.last_output_tab_index

        PluginManager.populate_default_settings("local", self.content)

    def get_output_metadata(self) -> dict[int, str]:
        return output_metadata.get(str(self.content), {})

    @run_in_background(name="LoadContent")
    def load_content(self, content: Path, /, frame: int | None = None, tab_index: int | None = None) -> None:
        super().load_content(content, frame, tab_index).result()

        get_loop().from_thread(
            self._autosave_timer.start,
            (self.global_settings.autosave.minute * 60 + self.global_settings.autosave.second) * 1000,
        )

    @run_in_background(name="ReloadContent")
    def reload_content(self) -> None:
        remaining_time = self._autosave_timer.remainingTime()

        loop = get_loop()
        loop.from_thread(self._autosave_timer.stop)

        super().reload_content().result()

        loop.from_thread(
            self._autosave_timer.start,
            remaining_time
            if remaining_time > 0
            else (self.global_settings.autosave.minute * 60 + self.global_settings.autosave.second) * 1000,
        )

    @run_in_loop(return_future=False)
    def clear_failed_load(self) -> None:
        self._autosave_timer.stop()
        super().clear_failed_load()

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

    def _on_local_settings_changed(self) -> None:
        if hasattr(self, "content"):
            self.tab_manager.sync_playhead_btn.setChecked(self.local_settings.synchronization.sync_playhead)
            self.tab_manager.sync_zoom_btn.setChecked(self.local_settings.synchronization.sync_zoom)
            self.tab_manager.sync_scroll_btn.setChecked(self.local_settings.synchronization.sync_scroll)
            self.tab_manager.autofit_btn.setChecked(self.local_settings.synchronization.autofit_all_views)

    def _on_playback_settings_changed(self, seek_step: int, speed: float, uncapped: bool) -> None:
        self.local_settings.playback.seek_step = seek_step

        super()._on_playback_settings_changed(seek_step, speed, uncapped)

    def _on_seek_step_reset(self) -> None:
        self.local_settings.playback.seek_step = None
        super()._on_seek_step_reset()


class VideoFileWorkspace(GenericFileWorkspace):
    title = "File"
    icon = IconName.FILE_VIDEO
    caption = "Open Video File"
    filters = (
        GenericFileWorkspace.FileFilter(
            "Video Files", ["mp4", "avi", "mkv", "mov", "webm", "flv", "wmv", "m2ts", "ts"]
        ),
        GenericFileWorkspace.FileFilter("Images Files", ["png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp", "ico"]),
    )

    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        # Pause console status to avoid conflict with BestSource's progress bar
        self.statusConsolePaused.emit()

        try:
            with self.env.use():
                if not hasattr(self.env.core, "bs"):
                    raise RuntimeError("The BestSource plugin 'bs' is required to load a file")

                if find_spec("vssource"):
                    from vssource import BestSource

                    clip = BestSource(show_pretty_progress=True).source(self.content, 0)
                else:
                    clip = self.env.core.bs.VideoSource(str(self.content))

                clip.set_output()

        finally:
            self.statusConsoleResumed.emit()
        logger.debug("Loaded file: %s", self.content)


class PythonScriptWorkspace(GenericFileWorkspace, VSEngineWorkspace[Path]):
    title = "Script"
    icon = IconName.FILE_TEXT
    caption = "Open VapourSynth Script"
    filters = (
        GenericFileWorkspace.FileFilter("Python Files", "py"),
        GenericFileWorkspace.FileFilter("VapourSynth Files", "vpy"),
    )

    content_type = "script"

    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        return super().loader()
