from __future__ import annotations

import ctypes
from collections.abc import Callable, Sequence
from logging import getLogger

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QWidget

from vsview.api import PluginAPI, run_in_background

logger = getLogger(__name__)


class CustomQImage(QImage):
    @property
    def ptr(self) -> int:
        return (
            0
            if self.isNull() or (bits := self.bits()) is None or len(bits) == 0
            else ctypes.addressof(ctypes.c_char.from_buffer(bits))
        )

    def write_to(
        self,
        data: npt.NDArray[np.uint8],
        target_format: QImage.Format,
        color_table: Sequence[int] | None = None,
    ) -> CustomQImage:
        h, w = data.shape[:2]

        if self.width() != w or self.height() != h or self.format() != target_format:
            image = CustomQImage(w, h, target_format)
        else:
            image = self

        if color_table is not None:
            image.setColorTable(color_table)

        stride = image.bytesPerLine()
        raw = np.frombuffer(image.bits(), dtype=np.uint8)

        if data.ndim == 2:
            img_data = raw.reshape((h, stride))[:, :w]
        else:
            channels = data.shape[2]
            img_data = raw.reshape((h, stride // channels, channels))[:, :w, :]

        img_data[:] = data
        return image


class CustomContextMenu(QMenu):
    def __init__(
        self,
        parent: QWidget,
        api: PluginAPI,
        image_provider: Callable[[], QPixmap] | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self._image_provider = image_provider or parent.grab

        self.copy_action = self.addAction("Copy Image to Clipboard")
        self.copy_action.triggered.connect(self._copy_image_to_clipboard)

        self.save_action = self.addAction("Save Image to Disk...")
        self.save_action.triggered.connect(self._on_save_image_action)

    @Slot()
    def _copy_image_to_clipboard(self) -> None:
        QApplication.clipboard().setPixmap(self._image_provider())
        logger.info("Copied image to clipboard")
        self.api.statusMessage.emit("Copied image to clipboard")

    @Slot()
    def _on_save_image_action(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self.parentWidget(), "Save Image", "", "PNG Files (*.png);;All Files (*)"
        )
        if file_path:
            logger.debug("Saving image to %s", file_path)
            self._save_image(self._image_provider().toImage(), file_path)

    @run_in_background(name="SavePlotImage")
    def _save_image(self, image: QImage, file_path: str, fmt: str = "PNG") -> None:
        self.api.statusMessage.emit("Saving image...")

        try:
            if not image.convertToFormat(QImage.Format.Format_RGB32).save(file_path, fmt):  # type: ignore[call-overload]
                raise OSError(f"Failed to save image to {file_path!r}")
        except Exception:
            logger.exception("Error saving image:")
            self.api.statusMessage.emit("Error saving image")
        else:
            logger.info("Saved image to %r", file_path)
            self.api.statusMessage.emit("Saved image")
