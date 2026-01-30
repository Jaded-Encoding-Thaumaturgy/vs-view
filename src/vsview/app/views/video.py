"""
Graphics view widget for displaying video frames.
"""

from __future__ import annotations

from logging import getLogger
from typing import NamedTuple

from jetpytools import clamp
from PySide6.QtCore import QEasingCurve, QSignalBlocker, Qt, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QContextMenuEvent, QCursor, QImage, QPixmap, QResizeEvent, QTransform, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QToolTip,
    QWidget,
    QWidgetAction,
)

from ...vsenv import run_in_background, run_in_loop
from ..settings import ActionID, SettingsManager, ShortcutManager

logger = getLogger(__name__)


class ViewState(NamedTuple):
    pixmap: QPixmap
    zoom: float
    autofit: bool
    h_ratio: float
    v_ratio: float
    slider_value: int

    @run_in_loop
    def apply_pixmap(self, view: GraphicsView, target_size: tuple[int, int] | None = None) -> None:
        pixmap = self.pixmap

        if target_size is not None and (pixmap.width(), pixmap.height()) != target_size:
            pixmap = pixmap.scaled(
                *target_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )

        view.pixmap_item.setPixmap(pixmap)
        view.setSceneRect(view.pixmap_item.boundingRect())
        view.loaded_once = False

    @run_in_loop(return_future=False)
    def apply_frozen_state(self, view: GraphicsView) -> None:
        if self.autofit:
            view.autofit = True
            view.autofit_action.setChecked(True)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.set_zoom(0)
        else:
            with QSignalBlocker(view.slider):
                view.slider.setValue(self.slider_value)

            view.set_zoom(self.zoom)
            self.restore_view_state(view)

    def restore_view_state(self, view: GraphicsView) -> None:
        if not self.autofit:
            if (h_bar := view.horizontalScrollBar()).maximum() > 0:
                h_bar.setValue(int(self.h_ratio * h_bar.maximum()))
            if (v_bar := view.verticalScrollBar()).maximum() > 0:
                v_bar.setValue(int(self.v_ratio * v_bar.maximum()))


class BaseGraphicsView(QGraphicsView):
    WHEEL_STEP = 15 * 8  # degrees

    wheelScrolled = Signal(int)

    # Status bar signals
    statusSavingImageStarted = Signal(str)  # message
    statusSavingImageFinished = Signal(str)  # completed message

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.angle_remainder = 0
        self.current_zoom = 1.0
        self.autofit = False

        self.zoom_factors = SettingsManager.global_settings.view.zoom_factors.copy()
        SettingsManager.signals.globalChanged.connect(self._on_settings_changed)

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.graphics_scene = QGraphicsScene(self)
        self.pixmap_item = self.graphics_scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.setScene(self.graphics_scene)

        self._zoom_animation = QVariantAnimation(self)
        self._zoom_animation.setDuration(150)
        self._zoom_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._zoom_animation.valueChanged.connect(self._apply_zoom_value)

        self.wheelScrolled.connect(self._on_wheel_scrolled)

        self.context_menu = QMenu(self)

        self.slider_container = QWidget(self)
        self.slider = QSlider(Qt.Orientation.Horizontal, self.slider_container)
        self.slider.setRange(0, 100)
        self.slider.setValue(self._zoom_to_slider(1.0))
        self.slider.setMinimumWidth(100)
        self.slider.setToolTip("1.00x")
        self.slider.valueChanged.connect(self._on_slider_value_changed)

        self.slider_layout = QHBoxLayout(self.slider_container)
        self.slider_layout.addWidget(QLabel("Zoom", self.slider_container))
        self.slider_layout.addWidget(self.slider)

        self.slider_container.setLayout(self.slider_layout)

        self.slider_action = QWidgetAction(self.context_menu)
        self.slider_action.setDefaultWidget(self.slider_container)

        self.context_menu.addAction(self.slider_action)
        self.context_menu.addSeparator()

        self.autofit_action = self.context_menu.addAction("Autofit")
        self.autofit_action.setCheckable(True)
        self.autofit_action.setChecked(self.autofit)
        self.autofit_action.triggered.connect(self._on_autofit_action)

        self.save_image_action = self.context_menu.addAction("Save Current Image")
        self.save_image_action.triggered.connect(self._on_save_image_action)

        self.copy_image_action = self.context_menu.addAction("Copy Image to Clipboard")
        self.copy_image_action.triggered.connect(self._copy_image_to_clipboard)

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        sm = ShortcutManager()
        sm.register_shortcut(ActionID.RESET_ZOOM, lambda: self.set_zoom(1.0), self)

        sm.register_action(ActionID.AUTOFIT, self.autofit_action)
        sm.register_action(ActionID.SAVE_CURRENT_IMAGE, self.save_image_action)
        sm.register_action(ActionID.COPY_IMAGE_TO_CLIPBOARD, self.copy_image_action)

        # Add actions to the widget so shortcuts work even when context menu is hidden
        self.addActions([self.autofit_action, self.save_image_action, self.copy_image_action])

    @property
    def state(self) -> ViewState:
        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()

        return ViewState(
            self.pixmap_item.pixmap().copy(),
            self.current_zoom,
            self.autofit,
            h_bar.value() / h_bar.maximum() if h_bar.maximum() > 0 else 0.5,
            v_bar.value() / v_bar.maximum() if v_bar.maximum() > 0 else 0.5,
            self.slider.value(),
        )

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.set_zoom(self.current_zoom)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.autofit:
            return event.ignore()

        modifier = event.modifiers()

        if modifier == Qt.KeyboardModifier.ControlModifier:
            angle_delta_y = event.angleDelta().y()

            # check if wheel wasn't rotated the other way since last rotation
            if self.angle_remainder * angle_delta_y < 0:
                self.angle_remainder = 0

            self.angle_remainder += angle_delta_y

            if abs(self.angle_remainder) >= self.WHEEL_STEP:
                self.wheelScrolled.emit(self.angle_remainder // self.WHEEL_STEP)
                self.angle_remainder %= self.WHEEL_STEP
            return

        if modifier == Qt.KeyboardModifier.ShiftModifier:
            # Translate vertical scroll to horizontal scroll
            delta = event.angleDelta().y()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            return

        super().wheelEvent(event)

    def set_zoom(self, value: float) -> None:
        target_zoom = value

        self.current_zoom = value / self.devicePixelRatio()

        if value == 0:
            if (pixmap := self.pixmap_item.pixmap()).isNull():
                return

            viewport = self.viewport()
            target_zoom = min(viewport.width() / pixmap.width(), viewport.height() / pixmap.height())

        current_scale = self.transform().m11()

        if current_scale == target_zoom:
            return

        self._zoom_animation.stop()
        self._zoom_animation.setStartValue(current_scale)
        self._zoom_animation.setEndValue(target_zoom)
        self._zoom_animation.start()

    def set_autofit(self, enabled: bool) -> None:
        self.autofit = enabled
        self.autofit_action.setChecked(self.autofit)

        if self.autofit:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.slider_container.setDisabled(True)
            self.set_zoom(0)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.slider_container.setDisabled(False)
            self.set_zoom(self._slider_to_zoom(self.slider.value()))

    def clear_scene(self) -> None:
        self.graphics_scene.clear()

    def reset_scene(self) -> None:
        self.clear_scene()

        self.pixmap_item = self.graphics_scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)

        self.setScene(self.graphics_scene)

    def _slider_to_zoom(self, slider_val: int) -> float:
        num_factors = len(self.zoom_factors)
        index = round(slider_val / 100.0 * (num_factors - 1))
        index = clamp(index, 0, num_factors - 1)
        return self.zoom_factors[index]

    def _zoom_to_slider(self, zoom: float) -> int:
        # Find the index of this zoom factor (or closest)
        try:
            index = self.zoom_factors.index(zoom)
        except ValueError:
            index = min(range(len(self.zoom_factors)), key=lambda i: abs(self.zoom_factors[i] - zoom))

        if (num_factors := len(self.zoom_factors)) <= 1:
            return 50

        return round(index / (num_factors - 1) * 100)

    def _on_settings_changed(self) -> None:
        new_factors = SettingsManager.global_settings.view.zoom_factors.copy()

        if new_factors != self.zoom_factors:
            current_zoom = self._slider_to_zoom(self.slider.value())
            self.zoom_factors = new_factors
            self.slider.setValue(self._zoom_to_slider(current_zoom))

    def _apply_zoom_value(self, value: float) -> None:
        self.setTransform(QTransform().scale(value, value))

    def _on_autofit_action(self) -> None:
        self.set_autofit(not self.autofit)

    def _on_slider_value_changed(self, value: int) -> None:
        zoom = self._slider_to_zoom(value)
        zoom_text = f"{zoom:.2f}x"
        self.slider.setToolTip(zoom_text)
        QToolTip.showText(QCursor.pos(), zoom_text, self.slider)
        self.set_zoom(zoom)

    def _on_wheel_scrolled(self, steps: int) -> None:
        # Calculate step size based on number of zoom factors
        num_factors = len(self.zoom_factors)
        step_size = 100 / (num_factors - 1) if num_factors > 1 else 100
        new_value = clamp(self.slider.value() + round(steps * step_size), 0, 100)
        self.slider.setValue(new_value)

    @Slot()
    def _on_save_image_action(self) -> None:
        if (pixmap := self.pixmap_item.pixmap()).isNull():
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image",
            "",
            "PNG Files (*.png);;All Files (*)",
        )

        if file_path:
            logger.debug("Saving image to %s", file_path)
            self._save_image(pixmap.toImage(), file_path)

    @run_in_background(name="SaveImage")
    def _save_image(self, image: QImage, file_path: str, fmt: str = "PNG") -> None:
        self.statusSavingImageStarted.emit("Saving image...")

        if image.format() == QImage.Format.Format_RGB30:
            image = image.convertToFormat(QImage.Format.Format_RGBA64)

        try:
            # The stubs are actually wrong here
            image.save(file_path, fmt, SettingsManager.global_settings.view.png_compression_level)  # type: ignore[call-overload]
        except Exception:
            logger.exception("Error saving image:")
        else:
            logger.info("Saved image to %r", file_path)
            self.statusSavingImageFinished.emit("Saved")

    @Slot()
    def _copy_image_to_clipboard(self) -> None:
        if (pixmap := self.pixmap_item.pixmap()).isNull():
            logger.error("No image to copy")
            return

        QApplication.clipboard().setPixmap(pixmap)
        logger.info("Copied image to clipboard")
        self.statusSavingImageFinished.emit("Copied image to clipboard")


class GraphicsView(BaseGraphicsView):
    zoomChanged = Signal(float)
    autofitChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.last_frame = 0
        self.loaded_once = False

    def set_zoom(self, value: float) -> None:
        super().set_zoom(value)

        if value:
            self.zoomChanged.emit(self.current_zoom)

    def _on_autofit_action(self) -> None:
        super()._on_autofit_action()

        self.autofitChanged.emit(self.autofit)
