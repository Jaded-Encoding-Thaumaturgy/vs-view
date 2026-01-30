from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Any, NamedTuple

from PySide6.QtCore import QSize, Qt, QTimer, Slot
from PySide6.QtGui import QPalette, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ...assets import IconName, IconReloadMixin, load_icon

if TYPE_CHECKING:
    from ..workspace import LoaderWorkspace


logger = getLogger(__name__)


class OutputInfo(NamedTuple):
    total_duration: str  # "1:23:04.500"
    total_frames: int  # 24000
    width: int  # 1920
    height: int  # 1080
    format_name: str  # "YUV420P16"
    fps: str  # "23.976"


class StatusWidget(IconReloadMixin, QWidget):
    """Custom status bar widget with sections for loading state and output information."""

    ICON_SIZE = QSize(18, 18)
    SPINNER_FRAMES = 8  # Number of rotation steps (360 / 8 = 45° per step)
    SPINNER_INTERVAL_MS = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Local settings signal connection (for showing "Settings saved" message)
        self._settings_manager.signals.localChanged.connect(self._on_settings_changed)

        self._reload_icons()  # Initial icon load
        self.register_icon_callback(self._reload_icons)

        self._setup_ui()
        self._setup_timers()

    def _reload_icons(self) -> None:
        color = self.palette().color(QPalette.ColorRole.WindowText)

        self.check_pixmap = load_icon(IconName.CHECK, self.ICON_SIZE, color)
        self.error_pixmap = load_icon(IconName.X_CIRCLE, self.ICON_SIZE, color)

        base_spinner = load_icon(IconName.SPINNER_GAP, self.ICON_SIZE, color)
        self.spinner_frames = self._generate_spinner_frames(base_spinner)

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(12)

        # Loading/Message section (left side)
        message_container = QWidget(self)
        message_layout = QHBoxLayout(message_container)
        message_layout.setContentsMargins(64, 0, 0, 0)
        message_layout.setSpacing(4)

        self.message_icon_label = QLabel(self)
        self.message_icon_label.setFixedSize(self.ICON_SIZE)
        self.message_text_label = QLabel(self)
        self.message_text_label.setMinimumWidth(138)

        message_layout.addWidget(self.message_icon_label)
        message_layout.addWidget(self.message_text_label)
        layout.addWidget(message_container)

        layout.addWidget(self._make_separator())

        # Duration section: "01:23:04"
        self.duration_label = QLabel(self)
        self.duration_label.setToolTip("Current position / Total duration")
        self.duration_label.setMinimumWidth(62)
        self.duration_label.setContentsMargins(0, 0, 0, 0)
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.duration_label)

        layout.addWidget(self._make_separator())

        # Frames section: "24000 frames"
        self.frames_label = QLabel(self)
        self.frames_label.setToolTip("Current frame / Total frames")
        self.frames_label.setMinimumWidth(70)
        self.frames_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.frames_label)

        layout.addWidget(self._make_separator())

        # Output info section: "1920x1080 YUV420P16 @23.976fps"
        self.output_info_label = QLabel(self)
        self.output_info_label.setToolTip("Resolution Format @FPS")
        layout.addWidget(self.output_info_label)

        # Settings saved section (right side, shows full path)
        self._settings_saved_container = QWidget(self)
        settings_saved_layout = QHBoxLayout(self._settings_saved_container)
        settings_saved_layout.setContentsMargins(0, 0, 0, 0)
        settings_saved_layout.setSpacing(4)

        self._settings_saved_separator = self._make_separator()
        self._settings_saved_icon = QLabel(self)
        self._settings_saved_icon.setFixedSize(self.ICON_SIZE)
        self._settings_saved_label = QLabel(self)

        settings_saved_layout.addWidget(self._settings_saved_separator)
        settings_saved_layout.addWidget(self._settings_saved_icon)
        settings_saved_layout.addWidget(self._settings_saved_label)

        self._settings_saved_container.setVisible(False)
        layout.addWidget(self._settings_saved_container)

        layout.addStretch()

        # Plugins section (right aligned)
        self.plugins_label = QLabel(self)
        layout.addWidget(self.plugins_label)

    def _setup_timers(self) -> None:
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self._clear_message)

        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(self.SPINNER_INTERVAL_MS)
        self._spinner_timer.timeout.connect(self._animate_spinner)

        self._settings_saved_timer = QTimer(self)
        self._settings_saved_timer.setSingleShot(True)
        self._settings_saved_timer.timeout.connect(self._clear_settings_saved)

        self._plugin_message_timer = QTimer(self)
        self._plugin_message_timer.setSingleShot(True)
        self._plugin_message_timer.timeout.connect(self._clear_plugin_message)

        self._is_loading = False
        self._spinner_frame_index = 0

    def _generate_spinner_frames(self, base_pixmap: QPixmap) -> list[QPixmap]:
        frames = list[QPixmap]()

        angle_step = 360 / self.SPINNER_FRAMES

        for i in range(self.SPINNER_FRAMES):
            transform = QTransform().rotate(i * angle_step)
            rotated = base_pixmap.transformed(transform)

            x = (rotated.width() - base_pixmap.width()) // 2
            y = (rotated.height() - base_pixmap.height()) // 2

            frames.append(rotated.copy(x, y, base_pixmap.width(), base_pixmap.height()))
        return frames

    @Slot()
    def _animate_spinner(self) -> None:
        self._spinner_frame_index = (self._spinner_frame_index + 1) % self.SPINNER_FRAMES
        self.message_icon_label.setPixmap(self.spinner_frames[self._spinner_frame_index])

    def _make_separator(self) -> QFrame:
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        return separator

    def deleteLater(self) -> None:
        self._settings_manager.signals.localChanged.disconnect(self._on_settings_changed)
        super().deleteLater()

    def start_loading(self, message: str) -> None:
        """Display a loading message with spinner indicator."""
        self._is_loading = True
        self._message_timer.stop()
        self._spinner_frame_index = 0
        self.message_icon_label.setPixmap(self.spinner_frames[0])
        self.message_text_label.setText(message)
        self._spinner_timer.start()

    def stop_loading(self, completed_message: str = "Completed", *, error: bool = False) -> None:
        """
        Hide the loading indicator and show a completion or error message.

        The completion message will auto-hide after some time.
        """
        self._is_loading = False
        self._spinner_timer.stop()
        self.message_icon_label.setPixmap(self.error_pixmap if error else self.check_pixmap)
        self.message_text_label.setText(completed_message)
        self._message_timer.start(self._settings_manager.global_settings.status_message_timeout)

    def error_loading(self, error_message: str) -> None:
        """
        Display an error message with an error icon.

        The error message will auto-hide after some time.
        """
        return self.stop_loading(error_message, error=True)

    def set_output_info(self, info: OutputInfo) -> None:
        """
        Update the persistent output information display.

        Args:
            info: OutputInfo dataclass containing all output details.
        """
        # Duration: "1:23:04.500"
        self.duration_label.setText(info.total_duration)

        # Frames: "24000 frames"
        self.frames_label.setText(f"{info.total_frames} frames")

        # Output info: "1920x1080 YUV420P16 @23.976fps"
        self.output_info_label.setText(f"{info.width}x{info.height} {info.format_name} @{info.fps}fps")

    @Slot(str)
    def set_plugin_message(self, message: str) -> None:
        """Display a message for a plugin."""
        self.plugins_label.setText(message)
        self._plugin_message_timer.start(self._settings_manager.global_settings.status_message_timeout)

    @Slot()
    def _clear_plugin_message(self) -> None:
        self.plugins_label.clear()

    def clear(self) -> None:
        """Reset all status sections to empty state."""
        self._message_timer.stop()
        self._plugin_message_timer.stop()
        self._is_loading = False
        self._spinner_timer.stop()
        self.message_icon_label.clear()
        self.message_text_label.clear()
        self.duration_label.clear()
        self.frames_label.clear()
        self.output_info_label.clear()
        self.plugins_label.clear()
        self._clear_settings_saved()

    def set_ready(self) -> None:
        self.message_icon_label.clear()
        self.message_text_label.setText("Ready")

    def connect_workspace(self, workspace: LoaderWorkspace[Any]) -> None:
        """Connect this status widget to a workspace's signals."""
        workspace.statusLoadingStarted.connect(self.start_loading)
        workspace.statusLoadingFinished.connect(self.stop_loading)
        workspace.statusLoadingErrored.connect(self.error_loading)
        workspace.statusOutputChanged.connect(self.set_output_info)

        workspace.playback.statusLoadingStarted.connect(self.start_loading)
        workspace.playback.statusLoadingFinished.connect(self.stop_loading)
        workspace.playback.statusLoadingErrored.connect(self.error_loading)

        workspace.tab_manager.statusLoadingStarted.connect(self.start_loading)
        workspace.tab_manager.statusLoadingFinished.connect(self.stop_loading)

        workspace.api.statusMessage.connect(self.set_plugin_message)

    def disconnect_workspace(self, workspace: LoaderWorkspace[Any]) -> None:
        """Disconnect this status widget from a workspace's signals."""
        self.clear()

        workspace.statusLoadingStarted.disconnect(self.start_loading)
        workspace.statusLoadingFinished.disconnect(self.stop_loading)
        workspace.statusLoadingErrored.disconnect(self.error_loading)
        workspace.statusOutputChanged.disconnect(self.set_output_info)

        workspace.playback.statusLoadingStarted.disconnect(self.start_loading)
        workspace.playback.statusLoadingFinished.disconnect(self.stop_loading)
        workspace.playback.statusLoadingErrored.disconnect(self.error_loading)

        workspace.tab_manager.statusLoadingStarted.disconnect(self.start_loading)
        workspace.tab_manager.statusLoadingFinished.disconnect(self.stop_loading)

        workspace.api.statusMessage.disconnect(self.set_plugin_message)

    @Slot()
    def _clear_message(self) -> None:
        if not self._is_loading:
            self.message_icon_label.clear()
            self.message_text_label.clear()

    def _on_settings_changed(self, path: str = "", message: str = "Settings saved") -> None:
        self._reload_icons()  # Hot-reload icons on settings change
        self._show_settings_saved(path, message)

    def _show_settings_saved(self, path: str, message: str) -> None:
        self._settings_saved_timer.stop()
        self._settings_saved_icon.setPixmap(self.check_pixmap)

        if path:
            self._settings_saved_label.setText(f"{message}: {path}")
            self._settings_saved_label.setToolTip(path)
        else:
            self._settings_saved_label.setText(message)
            self._settings_saved_label.setToolTip("")

        self._settings_saved_container.setVisible(True)
        self._settings_saved_timer.start(self._settings_manager.global_settings.status_message_timeout)

    @Slot()
    def _clear_settings_saved(self) -> None:
        self._settings_saved_timer.stop()
        self._settings_saved_container.setVisible(False)
        self._settings_saved_icon.clear()
        self._settings_saved_label.clear()
        self._settings_saved_label.setToolTip("")
