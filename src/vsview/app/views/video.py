"""
Graphics view widget for displaying video frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Flag, auto
from logging import getLogger
from math import isclose
from typing import Any, Literal, NamedTuple

from jetpytools import cachedproperty, clamp, copy_signature, cround
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSignalBlocker,
    Qt,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QStyleOptionGraphicsItem,
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

    @run_in_loop(return_future=False)
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


class RectSelectionHandle(Flag):
    """The eight directional resize handles of a rectangular selection."""

    NORTH = auto()
    SOUTH = auto()
    EAST = auto()
    WEST = auto()
    NORTH_WEST = NORTH | WEST
    NORTH_EAST = NORTH | EAST
    SOUTH_WEST = SOUTH | WEST
    SOUTH_EAST = SOUTH | EAST

    @cachedproperty
    def cursor(self) -> Qt.CursorShape:
        return {
            RectSelectionHandle.NORTH: Qt.CursorShape.SizeVerCursor,
            RectSelectionHandle.SOUTH: Qt.CursorShape.SizeVerCursor,
            RectSelectionHandle.EAST: Qt.CursorShape.SizeHorCursor,
            RectSelectionHandle.WEST: Qt.CursorShape.SizeHorCursor,
            RectSelectionHandle.NORTH_WEST: Qt.CursorShape.SizeFDiagCursor,
            RectSelectionHandle.NORTH_EAST: Qt.CursorShape.SizeBDiagCursor,
            RectSelectionHandle.SOUTH_WEST: Qt.CursorShape.SizeBDiagCursor,
            RectSelectionHandle.SOUTH_EAST: Qt.CursorShape.SizeFDiagCursor,
        }[self]

    @staticmethod
    def compute_handle_pos(rect: QRect | QRectF) -> dict[RectSelectionHandle, QPointF]:
        """
        Compute the center positions for all eight resize handles of a selection rect.

        Corner handles are listed before edge handles so that iterating in order
        gives corners priority during hit-testing.
        """
        if isinstance(rect, QRect):
            rect = rect.toRectF()
        return {
            RectSelectionHandle.NORTH_WEST: rect.topLeft(),
            RectSelectionHandle.NORTH_EAST: rect.topRight(),
            RectSelectionHandle.SOUTH_WEST: rect.bottomLeft(),
            RectSelectionHandle.SOUTH_EAST: rect.bottomRight(),
            RectSelectionHandle.NORTH: QPointF(rect.center().x(), rect.top()),
            RectSelectionHandle.SOUTH: QPointF(rect.center().x(), rect.bottom()),
            RectSelectionHandle.WEST: QPointF(rect.left(), rect.center().y()),
            RectSelectionHandle.EAST: QPointF(rect.right(), rect.center().y()),
        }


@dataclass(slots=True)
class RectSelectionDragState:
    """Transient state tracked while the user is actively dragging a rect selection."""

    mode: Literal["create", "move", "resize"]
    origin: tuple[float, float]
    initial_rect: QRect
    restore_rect: QRect
    handle: RectSelectionHandle | None = None
    did_update: bool = False


class RectSelectionOverlay(QGraphicsObject):
    """
    Semi-transparent overlay drawn on top of the pixmap item to visualise a rectangular selection region.
    The area outside the selection is darkened and the selection border + resize handles are painted.

    This item does not handle any mouse input itself. All interaction is managed by `BaseGraphicsView`.
    """

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setVisible(False)
        self.setZValue(1)
        self._image_rect = QRectF()
        self._selection_rect = QRectF()
        self._editable = False

        settings = SettingsManager.global_settings.view
        self._shade_color = QColor.fromRgbF(0.0, 0.0, 0.0, settings.shade_opacity)
        self._selection_color = QColor(settings.selection_outline_color)

    @property
    def image_rect(self) -> QRectF:
        return self._image_rect

    @image_rect.setter
    def image_rect(self, rect: QRectF) -> None:
        """Update the image bounding rect used to paint the darkened shade region."""
        if self._image_rect != rect:
            self.prepareGeometryChange()
            self._image_rect = rect

        self._update_visibility()
        self.update()

    @property
    def selection_rect(self) -> QRectF:
        return self._selection_rect

    @selection_rect.setter
    def selection_rect(self, rect: QRect | QRectF) -> None:
        """Set the selection rect. An empty rect hides the overlay."""
        self._selection_rect = QRectF(rect)
        self._update_visibility()
        self.update()

    @property
    def editable(self) -> bool:
        return self._editable

    @editable.setter
    def editable(self, editable: bool) -> None:
        """Toggle whether resize handles are painted on the selection border."""
        if self._editable == editable:
            return

        self._editable = editable
        self.update()

    @property
    def shade_opacity(self) -> float:
        return self._shade_color.alphaF()

    @shade_opacity.setter
    def shade_opacity(self, opacity: float) -> None:
        if self._shade_color.alphaF() != opacity:
            self._shade_color.setAlphaF(opacity)
            self.update()

    @property
    def selection_color(self) -> QColor:
        return self._selection_color

    @selection_color.setter
    def selection_color(self, color: QColor | str) -> None:
        new_color = QColor(color)
        if self._selection_color != new_color:
            self._selection_color = new_color
            self.update()

    def boundingRect(self) -> QRectF:
        return self._image_rect

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        if self._image_rect.isEmpty() or self._selection_rect.isEmpty():
            return

        if (rect := self._selection_rect.intersected(self._image_rect)).isEmpty():
            return

        painter.fillRect(
            QRectF(
                self._image_rect.left(),
                self._image_rect.top(),
                rect.left(),
                self._image_rect.height(),
            ),
            self._shade_color,
        )
        painter.fillRect(
            QRectF(
                rect.left(),
                self._image_rect.top(),
                rect.width(),
                rect.top() - self._image_rect.top(),
            ),
            self._shade_color,
        )
        painter.fillRect(
            QRectF(
                rect.right(),
                self._image_rect.top(),
                self._image_rect.right() - rect.right(),
                self._image_rect.height(),
            ),
            self._shade_color,
        )
        painter.fillRect(
            QRectF(
                rect.left(),
                rect.bottom(),
                rect.width(),
                self._image_rect.bottom() - rect.bottom(),
            ),
            self._shade_color,
        )

        pen = QPen(self._selection_color)
        pen.setCosmetic(True)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        if not self._editable:
            return

        scale_x = max(abs(painter.worldTransform().m11()), 1e-6)
        scale_y = max(abs(painter.worldTransform().m22()), 1e-6)
        handle_width = 8.0 / scale_x
        handle_height = 8.0 / scale_y

        handle_pen = QPen(QColor(20, 20, 20))
        handle_pen.setCosmetic(True)
        handle_pen.setWidth(1)
        painter.setPen(handle_pen)
        painter.setBrush(self._selection_color)

        for center in RectSelectionHandle.compute_handle_pos(rect).values():
            painter.drawRect(
                QRectF(
                    center.x() - handle_width / 2.0,
                    center.y() - handle_height / 2.0,
                    handle_width,
                    handle_height,
                )
            )

    def _update_visibility(self) -> None:
        self.setVisible(not self._image_rect.isEmpty() and not self._selection_rect.isEmpty())


class BaseGraphicsView(QGraphicsView):
    WHEEL_STEP = 15 * 8  # degrees

    wheelScrolled = Signal(int)

    # Status bar signals
    statusSavingImageStarted = Signal(str)  # message
    statusSavingImageFinished = Signal(str)  # completed message

    displayTransformChanged = Signal(QTransform)
    contextMenuRequested = Signal(QContextMenuEvent)
    rectSelectionChanged = Signal(QRect)
    rectSelectionFinished = Signal(QRect)

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
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        self.graphics_scene = QGraphicsScene(self)

        self._checkerboard = self._create_checkerboard_pixmap()

        self.pixmap_item = self.graphics_scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.setScene(self.graphics_scene)

        self._rect_selection = QRect()
        self._rect_selection_enabled = False
        self._rect_selection_drag: RectSelectionDragState | None = None
        self._init_rect_selection_overlay()

        self._zoom_animation = QVariantAnimation(self)
        self._zoom_animation.setDuration(125)
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

    @property
    def rect_selection(self) -> QRect:
        """Return the current rectangular selection in source image pixel coordinates (empty if cleared)."""
        return QRect(self._rect_selection)

    @property
    def rect_selection_enabled(self) -> bool:
        """Return whether rectangular selection overlay is active and editable."""
        return self._rect_selection_enabled

    @rect_selection_enabled.setter
    def rect_selection_enabled(self, enabled: bool) -> None:
        """Enable or disable the rectangular selection overlay editing mode."""
        if self._rect_selection_enabled == enabled:
            self._update_rect_selection_cursor()
            return

        self._rect_selection_enabled = enabled
        self._rect_selection_overlay.editable = enabled

        if not enabled:
            self._rect_selection_drag = None

        self._update_rect_selection_cursor()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.contextMenuRequested.emit(event)

        if not event.isAccepted():
            return

        self.context_menu.exec(event.globalPos())

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        if not Shiboken.isValid(self.pixmap_item) or self.pixmap_item.pixmap().isNull():
            return super().drawBackground(painter, rect)

        pixmap_rect = self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect())

        if (visible_rect := QRectF(rect).intersected(pixmap_rect)).isEmpty() or (zoom := self.transform().m11()) <= 0:
            return super().drawBackground(painter, rect)

        # Create brush with inverse zoom so the pattern stays fixed size on screen
        brush = QBrush(self._checkerboard)
        brush.setTransform(QTransform.fromScale(1.0 / zoom, 1.0 / zoom))

        painter.fillRect(visible_rect, brush)

        return None

    def viewportEvent(self, event: QEvent) -> bool:
        if not isinstance(event, QNativeGestureEvent) or event.gestureType() != Qt.NativeGestureType.ZoomNativeGesture:
            return super().viewportEvent(event)

        delta = event.value()
        if isclose(delta, 0, rel_tol=1e-6) or self.autofit:
            return False

        new_zoom = self.current_zoom * (1.0 + delta)
        new_zoom = clamp(new_zoom, self.zoom_factors[0], self.zoom_factors[-1])

        if isclose(new_zoom, self.current_zoom, rel_tol=1e-6):
            return True

        self.set_zoom(new_zoom)

        with QSignalBlocker(self.slider):
            self.slider.setValue(self._zoom_to_slider(new_zoom))

        return True

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
            return None

        if modifier == Qt.KeyboardModifier.ShiftModifier:
            # Translate vertical scroll to horizontal scroll
            delta = event.angleDelta().y()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            return None

        return super().wheelEvent(event)

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

        if (
            animated
            and min(current_scale, target_zoom) >= self.zoom_factors[0]
            and SettingsManager.global_settings.view.zoom_animation
        ):
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
        self._init_rect_selection_overlay()

        # Re-apply SAR transform if it was enabled
        self._update_sar_transform()

        self.setScene(self.graphics_scene)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        pixmap.setDevicePixelRatio(self.devicePixelRatio())
        old_size = self.pixmap_item.pixmap().size()
        self.pixmap_item.setPixmap(pixmap)
        self._update_rect_selection_overlay()

        if old_size != pixmap.size():
            self.update_scene_rect()

            if self.autofit:
                self.set_zoom(0, animated=False)

    def update_scene_rect(self) -> None:
        self.setSceneRect(self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect()))
        self.viewport().updateGeometry()
        self._update_rect_selection_overlay()

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
        has_sar = isclose(sar, 1.0)

        if self.apply_sar_action.isEnabled() != has_sar:
            self.apply_sar_action.setEnabled(has_sar)

        if not has_sar:
            self._set_sar_applied(False)
        else:
            self._update_sar_transform()

    def map_to_image(self, point: QPoint | QPointF) -> QPointF:
        """
        Map a point from view coordinates to the source image's pixel coordinate space.

        Args:
            point: The point in viewport or widget local coordinates.

        Returns:
            The corresponding point in the source image's pixel coordinates.
        """
        point = point.toPoint() if isinstance(point, QPointF) else point
        item_pos = self.pixmap_item.mapFromScene(self.mapToScene(point))

        scale_x, scale_y = self._image_scale_factors

        return QPointF(item_pos.x() * scale_x, item_pos.y() * scale_y)

    def set_rect_selection(self, rect: QRect, *, finished: bool = False) -> None:
        """
        Set the rectangular selection to the given QRect in source image coordinates.

        Args:
            rect: The bounding QRect, or an empty QRect to clear.
            finished: If True, emits rectSelectionFinished signal as well.
        """
        self._set_rect_selection(rect, emit_changed=True, emit_finished=finished)

    def clear_rect_selection(self) -> None:
        """Clear the current rectangular selection and emit signals."""
        self._set_rect_selection(QRect(), emit_changed=True, emit_finished=True)

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

    def _init_rect_selection_overlay(self) -> None:
        self._rect_selection_overlay = RectSelectionOverlay(self.pixmap_item)
        self._update_rect_selection_overlay()

    def _update_rect_selection_overlay(self) -> None:
        self._rect_selection_overlay.image_rect = self.pixmap_item.boundingRect()
        self._rect_selection_overlay.editable = self._rect_selection_enabled
        # Convert image rect to item rect
        scale_x, scale_y = self._image_scale_factors
        rectf = self._rect_selection.toRectF()
        rectf = QRectF(rectf.x() / scale_x, rectf.y() / scale_y, rectf.width() / scale_x, rectf.height() / scale_y)
        self._rect_selection_overlay.selection_rect = rectf

    def _set_rect_selection(self, rect: QRect, *, emit_changed: bool, emit_finished: bool) -> None:
        rect = self._normalize_rect_selection(rect)

        if self._rect_selection == rect:
            if emit_finished:
                self.rectSelectionFinished.emit(self.rect_selection)
            return

        self._rect_selection = QRect(rect)
        self._update_rect_selection_overlay()

        if emit_changed:
            self.rectSelectionChanged.emit(self.rect_selection)

        if emit_finished:
            self.rectSelectionFinished.emit(self.rect_selection)

    def _normalize_rect_selection(self, rect: QRect) -> QRect:
        """
        Clamp a rect to image bounds.
        Return an empty QRect if the result is degenerate.
        """
        if rect.isEmpty() or (pixmap := self.pixmap_item.pixmap()).isNull():
            return QRect()

        image_w, image_h = pixmap.width(), pixmap.height()
        x0, y0, x1, y1 = self._rect_selection_edges(rect)

        x0, x1 = clamp(x0, 0, image_w), clamp(x1, 0, image_w)
        y0, y1 = clamp(y0, 0, image_h), clamp(y1, 0, image_h)

        if x1 <= x0 or y1 <= y0:
            return QRect()

        return QRect(x0, y0, x1 - x0, y1 - y0)

    @property
    def _image_rect(self) -> QRectF:
        pixmap = self.pixmap_item.pixmap()
        return QRectF(0.0, 0.0, pixmap.width(), pixmap.height())

    @property
    def _image_scale_factors(self) -> tuple[float, float]:
        if (pixmap := self.pixmap_item.pixmap()).isNull() or (bounds := self.pixmap_item.boundingRect()).isEmpty():
            return 1.0, 1.0

        return pixmap.width() / bounds.width(), pixmap.height() / bounds.height()

    def _clamp_image_pos(self, pos: QPointF) -> QPointF:
        image_rect = self._image_rect
        return QPointF(
            clamp(pos.x(), image_rect.left(), image_rect.right()),
            clamp(pos.y(), image_rect.top(), image_rect.bottom()),
        )

    @staticmethod
    def _rect_selection_edges(rect: QRect) -> tuple[int, int, int, int]:
        """Return (x0, y0, x1, y1) using exclusive-end convention (not Qt's right/bottom)."""
        return rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()

    def _rect_selection_handle_at(self, pos: QPointF) -> RectSelectionHandle | None:
        """Hit-test which resize handle the given image-space position falls on, or None."""
        if self._rect_selection.isEmpty():
            return None

        item_transform = self.pixmap_item.transform()
        scale_x, scale_y = (
            max(abs(self.transform().m11() * item_transform.m11()), 1e-6),
            max(abs(self.transform().m22() * item_transform.m22()), 1e-6),
        )
        tolerance_x = 8.0 / scale_x
        tolerance_y = 8.0 / scale_y

        # Corners are tested before edges (iteration order of compute_handle_pos)
        for handle, center in RectSelectionHandle.compute_handle_pos(self._rect_selection).items():
            handle_rect = QRectF(
                center.x() - tolerance_x / 2.0,
                center.y() - tolerance_y / 2.0,
                tolerance_x,
                tolerance_y,
            )
            if handle_rect.contains(pos):
                return handle

        return None

    def _update_rect_selection_cursor(self, pos: QPoint | QPointF | None = None) -> None:
        if not self._rect_selection_enabled:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if pos is None:
            pos = self.viewport().mapFromGlobal(QCursor.pos())

        image_pos = self.map_to_image(pos)
        if not self._image_rect.contains(image_pos):
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return

        handle = self._rect_selection_handle_at(image_pos)

        if handle is not None:
            self.viewport().setCursor(handle.cursor)
        elif not self._rect_selection.isEmpty() and QRectF(self._rect_selection).contains(image_pos):
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _start_rect_selection_drag(self, event: QMouseEvent) -> bool:
        if (
            not self._rect_selection_enabled
            or self.pixmap_item.pixmap().isNull()
            or event.button() != Qt.MouseButton.LeftButton
        ):
            return False

        image_pos = self.map_to_image(event.position())
        if not self._image_rect.contains(image_pos):
            return False

        image_pos = self._clamp_image_pos(image_pos)
        handle = self._rect_selection_handle_at(image_pos)
        mode: Literal["create", "move", "resize"]

        if handle is not None and not self._rect_selection.isEmpty():
            mode = "resize"
        elif handle is None and not self._rect_selection.isEmpty() and QRectF(self._rect_selection).contains(image_pos):
            mode = "move"
        else:
            mode = "create"

        self._rect_selection_drag = RectSelectionDragState(
            mode=mode,
            origin=(image_pos.x(), image_pos.y()),
            initial_rect=self.rect_selection,
            restore_rect=self.rect_selection,
            handle=handle,
        )

        if mode == "move":
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode == "resize" and handle is not None:
            self.viewport().setCursor(handle.cursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

        event.accept()
        return True

    def _update_rect_selection_drag(self, event: QMouseEvent) -> bool:
        if self._rect_selection_drag is None:
            return False

        image_pos = self._clamp_image_pos(self.map_to_image(event.position()))
        drag = self._rect_selection_drag

        if drag.mode == "create":
            rect = self._rect_from_points(QPointF(*drag.origin), image_pos)
        elif drag.mode == "move" and not drag.initial_rect.isEmpty():
            rect = self._move_rect_selection(drag.initial_rect, QPointF(*drag.origin), image_pos)
        elif drag.mode == "resize" and not drag.initial_rect.isEmpty() and drag.handle is not None:
            rect = self._resize_rect_selection(drag.initial_rect, image_pos, drag.handle)
        else:
            rect = QRect()

        if not rect.isEmpty():
            self._set_rect_selection(rect, emit_changed=True, emit_finished=False)
            drag.did_update = True

        event.accept()
        return True

    def _finish_rect_selection_drag(self, event: QMouseEvent) -> bool:
        if self._rect_selection_drag is None or event.button() != Qt.MouseButton.LeftButton:
            return False

        drag = self._rect_selection_drag
        self._rect_selection_drag = None

        if not drag.did_update:
            # Click without drag in create mode clears the selection
            # Click without drag on a handle/interior preserves the selection
            rect = QRect() if drag.mode == "create" else drag.restore_rect
            self._set_rect_selection(rect, emit_changed=True, emit_finished=True)
        else:
            self.rectSelectionFinished.emit(self.rect_selection)

        self._update_rect_selection_cursor(event.position())
        event.accept()
        return True

    def _cancel_rect_selection_drag(self, event: QKeyEvent) -> bool:
        if self._rect_selection_drag is None or event.key() != Qt.Key.Key_Escape:
            return False

        restore_rect = self._rect_selection_drag.restore_rect
        self._rect_selection_drag = None
        self._set_rect_selection(restore_rect, emit_changed=True, emit_finished=True)
        self._update_rect_selection_cursor()

        event.accept()
        return True

    def _rect_from_points(self, start: QPointF, end: QPointF) -> QRect:
        """Build a normalized rect from two corner points."""
        x0 = cround(min(start.x(), end.x()))
        y0 = cround(min(start.y(), end.y()))
        x1 = cround(max(start.x(), end.x()))
        y1 = cround(max(start.y(), end.y()))
        return self._normalize_rect_selection(QRect(x0, y0, x1 - x0, y1 - y0))

    def _move_rect_selection(self, rect: QRect, start: QPointF, end: QPointF) -> QRect:
        """Translate the selection rect by the drag delta, clamping to image bounds."""
        pixmap = self.pixmap_item.pixmap()

        image_w, image_h = pixmap.width(), pixmap.height()
        dx = cround(end.x() - start.x())
        dy = cround(end.y() - start.y())

        x0 = clamp(rect.x() + dx, 0, max(image_w - rect.width(), 0))
        y0 = clamp(rect.y() + dy, 0, max(image_h - rect.height(), 0))
        return QRect(x0, y0, rect.width(), rect.height())

    def _resize_rect_selection(self, rect: QRect, pos: QPointF, handle: RectSelectionHandle) -> QRect:
        """Resize the selection by moving the edge(s) corresponding to the active handle."""
        pixmap = self.pixmap_item.pixmap()

        image_w, image_h = pixmap.width(), pixmap.height()
        x0, y0, x1, y1 = self._rect_selection_edges(rect)
        px = cround(pos.x())
        py = cround(pos.y())

        if handle & RectSelectionHandle.WEST:
            x0 = clamp(px, 0, x1 - 1)
        if handle & RectSelectionHandle.EAST:
            x1 = clamp(px, x0 + 1, image_w)
        if handle & RectSelectionHandle.NORTH:
            y0 = clamp(py, 0, y1 - 1)
        if handle & RectSelectionHandle.SOUTH:
            y1 = clamp(py, y0 + 1, image_h)

        return self._normalize_rect_selection(QRect(x0, y0, x1 - x0, y1 - y0))

    def _slider_to_zoom(self, slider_val: int) -> float:
        num_factors = len(self.zoom_factors)
        index = cround(slider_val / 100.0 * (num_factors - 1))
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

        return cround(index / (num_factors - 1) * 100)

    def _on_settings_changed(self) -> None:
        settings = SettingsManager.global_settings.view
        new_factors = settings.zoom_factors.copy()

        if new_factors != self.zoom_factors:
            current_zoom = self._slider_to_zoom(self.slider.value())
            self.zoom_factors = new_factors
            self.slider.setValue(self._zoom_to_slider(current_zoom))

        if Shiboken.isValid(self._rect_selection_overlay):
            self._rect_selection_overlay.shade_opacity = settings.shade_opacity
            self._rect_selection_overlay.selection_color = settings.selection_outline_color

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
        new_value = clamp(self.slider.value() + cround(steps * step_size), 0, 100)
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
        if self._update_rect_selection_drag(event):
            if self.isVisible():
                self.mouseMoved.emit(event)
            return

        super().mouseMoveEvent(event)

        if self._rect_selection_enabled:
            self._update_rect_selection_cursor(event.position())

        if self.hasMouseTracking() and self.isVisible():
            self.mouseMoved.emit(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._start_rect_selection_drag(event):
            if self.isVisible():
                self.mousePressed.emit(event)
            return

        super().mousePressEvent(event)

        if self.isVisible():
            self.mousePressed.emit(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._finish_rect_selection_drag(event):
            if self.isVisible():
                self.mouseReleased.emit(event)
            return

        super().mouseReleaseEvent(event)

        if self.isVisible():
            self.mouseReleased.emit(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._cancel_rect_selection_drag(event):
            if self.isVisible():
                self.keyPressed.emit(event)
            return

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
