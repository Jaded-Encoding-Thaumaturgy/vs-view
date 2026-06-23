from __future__ import annotations

from logging import getLogger

from PySide6.QtCore import Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QWidget

from vsview.api import PluginAPI, run_in_background

logger = getLogger(__name__)


class CustomContextMenu(QMenu):
    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api

        self.copy_action = self.addAction("Copy Image to Clipboard")
        self.copy_action.triggered.connect(self._copy_image_to_clipboard)

        self.save_action = self.addAction("Save Image to Disk...")
        self.save_action.triggered.connect(self._on_save_image_action)

    @property
    def safe_parent(self) -> QWidget:
        if isinstance(p := super().parent(), QWidget):
            return p
        raise NotImplementedError

    @Slot()
    def _copy_image_to_clipboard(self) -> None:
        QApplication.clipboard().setPixmap(self.safe_parent.grab())
        logger.info("Copied image to clipboard")
        self.api.statusMessage.emit("Copied image to clipboard")

    @Slot()
    def _on_save_image_action(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self.safe_parent, "Save Image", "", "PNG Files (*.png);;All Files (*)"
        )
        if file_path:
            logger.debug("Saving image to %s", file_path)
            self._save_image(self.safe_parent.grab().toImage(), file_path)

    @run_in_background(name="SavePlotImage")
    def _save_image(self, image: QImage, file_path: str, fmt: str = "PNG") -> None:
        self.api.statusMessage.emit("Saving image...")

        try:
            image.convertToFormat(QImage.Format.Format_RGB32).save(file_path, fmt)  # type: ignore[call-overload]
        except Exception:
            logger.exception("Error saving image:")
            self.api.statusMessage.emit("Error saving image")
        else:
            logger.info("Saved image to %r", file_path)
            self.api.statusMessage.emit("Saved image")
