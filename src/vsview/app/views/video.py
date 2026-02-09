"""
Graphics view widget for displaying video frames.
"""

from __future__ import annotations

from logging import getLogger
from typing import Any, NamedTuple

from jetpytools import clamp, copy_signature
from PySide6.QtCore import QEasingCurve, QRect, QRectF, QSignalBlocker, Qt, QVariantAnimation, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QContextMenuEvent,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
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
from shiboken6 import Shiboken

from ...vsenv import run_in_background, run_in_loop
from ..settings import ActionID, SettingsManager, ShortcutManager

logger = getLogger(__name__)


class ViewState(NamedTuple):
    pixmap: QPixmap
    zoom: float
    autofit: bool
    scene_x: float
    scene_y: float
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

        view.set_pixmap(pixmap)

    @run_in_loop(return_future=False)
    def apply_frozen_state(self, view: GraphicsView) -> None:
        if self.autofit:
            view.set_autofit(True, animated=False)
        else:
            with QSignalBlocker(view.slider):
                view.slider.setValue(self.slider_value)

            view.set_zoom(self.zoom, animated=False)
            self.restore_view_state(view)

    def restore_view_state(self, view: GraphicsView) -> None:
        if not self.autofit:
            view.update_center((self.scene_x, self.scene_y))


class BaseGraphicsView(QGraphicsView):
    WHEEL_STEP = 15 * 8  # degrees

    wheelScrolled = Signal(int)

    # Status bar signals
    statusSavingImageStarted = Signal(str)  # message
    statusSavingImageFinished = Signal(str)  # completed message

    displayTransformChanged = Signal(QTransform)
    contextMenuRequested = Signal(QContextMenuEvent)

    @copy_signature(QGraphicsView.__init__)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.angle_remainder = 0
        self.current_zoom = 1.0
        self.autofit = False

        self._sar = 1.0
        self._sar_applied = False

        self.zoom_factors = SettingsManager.global_settings.view.zoom_factors.copy()
        SettingsManager.signals.globalChanged.connect(self._on_settings_changed)

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.graphics_scene = QGraphicsScene(self)

        self._checkerboard = self._create_checkerboard_pixmap()

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

        self.apply_sar_action = self.context_menu.addAction("Toggle SAR")
        self.apply_sar_action.setCheckable(True)
        self.apply_sar_action.setChecked(self._sar_applied)
        self.apply_sar_action.setEnabled(False)  # Disabled until SAR != 1.0
        self.apply_sar_action.triggered.connect(self._set_sar_applied)

        self.save_image_action = self.context_menu.addAction("Save Current Image")
        self.save_image_action.triggered.connect(self._on_save_image_action)

        self.copy_image_action = self.context_menu.addAction("Copy Image to Clipboard")
        self.copy_image_action.triggered.connect(self._copy_image_to_clipboard)

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        sm = ShortcutManager()
        sm.register_shortcut(ActionID.RESET_ZOOM, lambda: self.slider.setValue(self._zoom_to_slider(1.0)), self)

        sm.register_action(ActionID.TOGGLE_SAR, self.apply_sar_action)
        sm.register_action(ActionID.AUTOFIT, self.autofit_action)
        sm.register_action(ActionID.SAVE_CURRENT_IMAGE, self.save_image_action)
        sm.register_action(ActionID.COPY_IMAGE_TO_CLIPBOARD, self.copy_image_action)

        # Add actions to the widget so shortcuts work even when context menu is hidden
        self.addActions([self.autofit_action, self.apply_sar_action, self.save_image_action, self.copy_image_action])

    @property
    def state(self) -> ViewState:
        center = self.mapToScene(self.viewport().rect().center())

        return ViewState(
            self.pixmap_item.pixmap().copy(),
            self.current_zoom,
            self.autofit,
            center.x(),
            center.y(),
            self.slider.value(),
        )

    @property
    def display_sar(self) -> float:
        return self._sar if self._sar_applied else 1.0

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.contextMenuRequested.emit(event)

        if not event.isAccepted():
            return

        self.context_menu.exec(event.globalPos())

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        if self.pixmap_item.pixmap().isNull():
            return super().drawBackground(painter, rect)

        pixmap_rect = self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect())

        if (visible_rect := QRectF(rect).intersected(pixmap_rect)).isEmpty() or (zoom := self.transform().m11()) <= 0:
            return super().drawBackground(painter, rect)

        # Create brush with inverse zoom so the pattern stays fixed size on screen
        brush = QBrush(self._checkerboard)
        brush.setTransform(QTransform.fromScale(1.0 / zoom, 1.0 / zoom))

        painter.fillRect(visible_rect, brush)

    def resizeEvent(self, event: QResizeEvent) -> None:
        if event.type() == QResizeEvent.Type.Resize:
            self.set_zoom(self.current_zoom if not self.autofit else 0)

        super().resizeEvent(event)

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

    def set_zoom(self, value: float, *, animated: bool = True) -> None:
        target_zoom = value

        if value:
            self.current_zoom = value

        if value == 0:
            if not Shiboken.isValid(self.pixmap_item) or self.pixmap_item.pixmap().isNull():
                return

            viewport = self.viewport()
            rect = self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect())
            target_zoom = min(viewport.width() / rect.width(), viewport.height() / rect.height())

        current_scale = self.transform().m11()

        if current_scale == target_zoom:
            return

        if animated and min(current_scale, target_zoom) >= self.zoom_factors[0]:
            self._zoom_animation.stop()
            self._zoom_animation.setStartValue(current_scale)
            self._zoom_animation.setEndValue(target_zoom)
            self._zoom_animation.start()
        else:
            self._apply_zoom_value(target_zoom)

    def set_autofit(self, enabled: bool, *, animated: bool = True) -> None:
        self.autofit = enabled
        self.autofit_action.setChecked(self.autofit)

        if self.autofit:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.slider_container.setDisabled(True)
            self.set_zoom(0, animated=animated)
        else:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.slider_container.setDisabled(False)
            self.set_zoom(self._slider_to_zoom(self.slider.value()), animated=animated)

    def clear_scene(self) -> None:
        self.graphics_scene.clear()

    def reset_scene(self) -> None:
        self.clear_scene()

        self.pixmap_item = self.graphics_scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)

        # Re-apply SAR transform if it was enabled
        self._update_sar_transform()

        self.setScene(self.graphics_scene)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        old_size = self.pixmap_item.pixmap().size()
        self.pixmap_item.setPixmap(pixmap)

        if old_size != pixmap.size():
            self.update_scene_rect()

            if self.autofit:
                self.set_zoom(0, animated=False)

    def update_scene_rect(self) -> None:
        self.setSceneRect(self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect()))
        self.viewport().updateGeometry()

    def update_center(self, ref: QGraphicsView | tuple[float, float], /) -> None:
        if isinstance(ref, QGraphicsView):
            center = ref.mapToScene(ref.viewport().rect().center())
            center_x, center_y = center.x(), center.y()
        else:
            center_x, center_y = ref

        # Compensate for centerOn's 1-pixel rounding drift
        zoom = self.transform().m11() or 1.0
        half_pixel = 0.5 / zoom
        self.centerOn(center_x + half_pixel, center_y + half_pixel)

    def set_sar(self, sar: float | None = None) -> None:
        sar = sar or 1.0

        if self._sar == sar:
            return

        self._sar = sar
        has_sar = sar != 1.0

        if self.apply_sar_action.isEnabled() != has_sar:
            self.apply_sar_action.setEnabled(has_sar)

        if not has_sar:
            self._set_sar_applied(False)
        else:
            self._update_sar_transform()

    @staticmethod
    def _create_checkerboard_pixmap() -> QPixmap:
        size = SettingsManager.global_settings.view.checkerboard_size
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.fill(Qt.GlobalColor.white)

        with QPainter(pixmap) as painter:
            painter.fillRect(0, 0, size, size, Qt.GlobalColor.lightGray)
            painter.fillRect(size, size, size, size, Qt.GlobalColor.lightGray)
        return pixmap

    @Slot(bool)
    def _set_sar_applied(self, applied: bool) -> None:
        if self._sar_applied == applied:
            return

        self._sar_applied = applied

        if self.apply_sar_action.isChecked() != applied:
            self.apply_sar_action.setChecked(applied)

        self._update_sar_transform()

    def _update_sar_transform(self) -> None:
        scale = self._sar if self._sar_applied else 1.0
        transform = QTransform().scale(scale, 1.0)

        if self.pixmap_item.transform() != transform:
            self.pixmap_item.setTransform(transform)
            self.displayTransformChanged.emit(transform)
            self.update_scene_rect()
            self.set_zoom(0 if self.autofit else self.current_zoom, animated=False)

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

    mouseMoved = Signal(QMouseEvent)
    mousePressed = Signal(QMouseEvent)
    mouseReleased = Signal(QMouseEvent)
    keyPressed = Signal(QKeyEvent)
    keyReleased = Signal(QKeyEvent)

    @copy_signature(QGraphicsView.__init__)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)

        if self.hasMouseTracking() and self.isVisible():
            self.mouseMoved.emit(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)

        if self.isVisible():
            self.mousePressed.emit(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)

        if self.isVisible():
            self.mouseReleased.emit(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)

        if self.isVisible():
            self.keyPressed.emit(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        super().keyReleaseEvent(event)

        if self.isVisible():
            self.keyReleased.emit(event)

    def set_zoom(self, value: float, *, animated: bool = True) -> None:
        super().set_zoom(value, animated=animated)

        if value:
            self.zoomChanged.emit(self.current_zoom)

    def _on_autofit_action(self) -> None:
        super()._on_autofit_action()

        self.autofitChanged.emit(self.autofit)
