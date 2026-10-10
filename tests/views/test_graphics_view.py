from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QNativeGestureEvent, QPainter, QPixmap, QPointingDevice, QTransform
from PySide6.QtWidgets import QStyleOptionGraphicsItem
from pytestqt.qtbot import QtBot

from vsview.app.views.video import GraphicsView, RectSelectionHandle, RectSelectionOverlay, ViewState

if TYPE_CHECKING:
    from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def _view_pos_for_image_point(view: GraphicsView, point: QPointF) -> QPointF:
    return QPointF(view.mapFromScene(view.pixmap_item.mapToScene(point)))


def map_from_image(view: GraphicsView, point: QPointF) -> QPointF:
    """Helper mapping an image coordinate to viewport space."""
    return _view_pos_for_image_point(view, point)


def test_map_to_image_is_stable_with_sar(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(100, 80)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_sar(2.0)
    view.sar_applied = True

    qtbot.waitUntil(view.isVisible, timeout=2000)

    image_point = QPointF(25, 30)
    mapped = view.map_to_image(_view_pos_for_image_point(view, image_point))

    assert mapped.toPoint() == image_point.toPoint()


def test_rect_selection_drag_creates_pixel_rect(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(120, 90)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.rect_selection_enabled = True

    qtbot.waitUntil(view.isVisible, timeout=2000)

    start = _view_pos_for_image_point(view, QPointF(10, 12)).toPoint()
    end = _view_pos_for_image_point(view, QPointF(42, 36)).toPoint()

    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(view.viewport(), pos=end)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)  # type: ignore[no-untyped-call]

    assert view.rect_selection == QRect(10, 12, 32, 24)


# Zoom & Slider Mapping
def test_zoom_to_slider_and_slider_to_zoom_conversions(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)

    assert view.slider_to_zoom(0) == view.zoom_factors[0]
    assert view.slider_to_zoom(100) == view.zoom_factors[-1]

    # Every registered zoom factor must roundtrip accurately
    for factor in view.zoom_factors:
        slider_val = view.zoom_to_slider(factor)
        assert 0 <= slider_val <= 100
        roundtrip_zoom = view.slider_to_zoom(slider_val)
        assert math.isclose(roundtrip_zoom, factor, rel_tol=1e-5)

    # Intermediate / unlisted float zooms map to the closest factor
    mid_zoom = (view.zoom_factors[0] + view.zoom_factors[1]) / 2.0
    slider_val = view.zoom_to_slider(mid_zoom)
    assert 0 <= slider_val <= 100

    # Bounds clamping for out-of-range slider values
    assert view.slider_to_zoom(-50) == view.zoom_factors[0]
    assert view.slider_to_zoom(200) == view.zoom_factors[-1]

    # Edge case: single zoom factor
    original_factors = view.zoom_factors.copy()
    view.zoom_factors = [1.0]
    assert view.zoom_to_slider(1.0) == 50
    assert view.slider_to_zoom(50) == 1.0
    view.zoom_factors = original_factors


def test_zoom_limits_and_gestures(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # Wheel scrolling limits at boundaries
    view.slider.setValue(100)
    view.wheelScrolled.emit(10)
    assert view.slider.value() == 100
    assert view.current_zoom == view.zoom_factors[-1]

    view.slider.setValue(0)
    view.wheelScrolled.emit(-10)
    assert view.slider.value() == 0
    assert view.current_zoom == view.zoom_factors[0]

    # Native gesture zoom respects min and max zoom bounds
    device = QPointingDevice.primaryPointingDevice()
    center_pt = QPointF(50, 50)

    # Exceedingly large zoom-in gesture clamps to max factor
    gesture_max = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        2,
        center_pt,
        center_pt,
        center_pt,
        200.0,
        QPointF(0, 0),
    )
    handled = view.viewportEvent(gesture_max)
    assert handled is True
    assert math.isclose(view.current_zoom, view.zoom_factors[-1], rel_tol=1e-5)

    # Exceedingly large zoom-out gesture clamps to min factor
    gesture_min = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        2,
        center_pt,
        center_pt,
        center_pt,
        -1.0,
        QPointF(0, 0),
    )
    handled = view.viewportEvent(gesture_min)
    assert handled is True
    assert math.isclose(view.current_zoom, view.zoom_factors[0], rel_tol=1e-5)

    # Zero/near-zero gesture delta returns False and does not alter zoom
    current = view.current_zoom
    gesture_zero = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        2,
        center_pt,
        center_pt,
        center_pt,
        0.0,
        QPointF(0, 0),
    )
    assert view.viewportEvent(gesture_zero) is False
    assert view.current_zoom == current

    # Direct set_zoom emits zoomChanged signal
    with qtbot.waitSignal(view.zoomChanged) as blocker:
        view.set_zoom(2.0)
    assert blocker.args == [2.0]
    assert math.isclose(view.transform().m11(), 2.0, rel_tol=1e-5)


def test_manual_zoom_disables_autofit_mode(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(200, 150)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    view.set_autofit(True)
    assert view.autofit is True
    assert view.autofit_action.isChecked() is True
    assert view.slider_container.isEnabled() is False
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    # While autofit is active, native gestures and wheel zoom are suppressed
    device = QPointingDevice.primaryPointingDevice()
    gesture = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        device,
        2,
        QPointF(50, 50),
        QPointF(50, 50),
        QPointF(50, 50),
        0.5,
        QPointF(0, 0),
    )
    assert view.viewportEvent(gesture) is False

    # Disabling autofit manually breaks out of autofit and re-enables slider controls
    view.set_autofit(False)
    assert view.autofit is False
    assert view.autofit_action.isChecked() is False
    assert view.slider_container.isEnabled() is True
    assert view.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert math.isclose(view.current_zoom, view.slider_to_zoom(view.slider.value()))


# Anamorphic Pixel Aspect Ratio (SAR)
def test_set_sar_and_sar_applied_state(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)

    # 1.0 SAR defaults
    assert view.sar == 1.0
    assert view.sar_applied is False
    assert view.display_sar == 1.0
    assert view.apply_sar_action.isEnabled() is False
    assert view.pixmap_item.transform().m11() == 1.0

    # Setting non-1.0 SAR enables action but does not apply scaling until requested
    view.set_sar(1.5)
    assert view.sar == 1.5
    assert view.apply_sar_action.isEnabled() is True
    assert view.sar_applied is False
    assert view.display_sar == 1.0
    assert view.pixmap_item.transform().m11() == 1.0

    # Applying SAR updates display_sar, item transform m11, and action check state
    view.sar_applied = True
    assert view.sar_applied is True
    assert view.apply_sar_action.isChecked() is True
    assert view.display_sar == 1.5
    assert view.pixmap_item.transform().m11() == 1.5

    # Toggling SAR off restores standard 1.0 item transform
    view.sar_applied = False
    assert view.sar_applied is False
    assert view.apply_sar_action.isChecked() is False
    assert view.display_sar == 1.0
    assert view.pixmap_item.transform().m11() == 1.0

    # Re-enabling SAR and then resetting SAR to 1.0 automatically clears applied state
    view.sar_applied = True
    view.set_sar(1.0)
    assert view.apply_sar_action.isEnabled() is False
    assert view.sar_applied is False
    assert view.pixmap_item.transform().m11() == 1.0


def test_sar_display_transform_signal(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(100, 80)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_sar(2.0)

    with qtbot.waitSignal(view.displayTransformChanged) as blocker:
        view.sar_applied = True

    emitted_transform = blocker.args[0]  # pyright: ignore[reportOptionalSubscript]
    assert isinstance(emitted_transform, QTransform)
    assert emitted_transform.m11() == 2.0


def test_coordinate_mapping_under_various_sar_and_zoom(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(500, 400)
    view.show()

    pixmap = QPixmap(200, 150)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    test_points = [
        QPointF(0, 0),
        QPointF(50, 40),
        QPointF(100, 75),
        QPointF(199, 149),
    ]

    for sar in (0.5, 1.5, 2.0):
        view.set_sar(sar)
        view.sar_applied = True

        for zoom in (1.0, 2.0):
            view.set_zoom(zoom)

            for pt in test_points:
                view_pos = map_from_image(view, pt)
                mapped_back = view.map_to_image(view_pos)
                assert abs(mapped_back.x() - pt.x()) <= 1.0
                assert abs(mapped_back.y() - pt.y()) <= 1.0

    # Under integer SAR (2.0) and integer coordinates, mapping is exact
    view.set_sar(2.0)
    view.sar_applied = True
    view.set_zoom(1.0)
    for pt in (QPointF(0, 0), QPointF(50, 40), QPointF(100, 70)):
        view_pos = map_from_image(view, pt)
        mapped_back = view.map_to_image(view_pos)
        assert mapped_back.toPoint() == pt.toPoint()

    # Fractional SAR and sub-1.0 zoom (quantization within 1 pixel)
    view.set_sar(1.3333)
    view.sar_applied = True
    view.set_zoom(0.5)
    for pt in test_points:
        view_pos = map_from_image(view, pt)
        mapped_back = view.map_to_image(view_pos)
        assert abs(mapped_back.x() - pt.x()) <= 1.0
        assert abs(mapped_back.y() - pt.y()) <= 1.0


# Autofit Mode
def test_autofit_mode_fits_image_to_viewport(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(800, 400)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    view.set_autofit(True)

    expected_zoom = min(view.viewport().width() / 800.0, view.viewport().height() / 400.0)
    assert math.isclose(view.transform().m11(), expected_zoom, rel_tol=1e-3)

    # Loading a different aspect ratio image recalculates autofit target zoom
    portrait_pixmap = QPixmap(300, 900)
    portrait_pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(portrait_pixmap)

    expected_portrait_zoom = min(view.viewport().width() / 300.0, view.viewport().height() / 900.0)
    assert math.isclose(view.transform().m11(), expected_portrait_zoom, rel_tol=1e-3)


def test_autofit_mode_recalculates_on_viewport_resize(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.show()

    pixmap = QPixmap(500, 500)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    view.set_autofit(True)

    # Shrinking viewport recalculates zoom downward
    view.resize(250, 250)
    qtbot.waitUntil(
        lambda: math.isclose(
            view.transform().m11(),
            min(view.viewport().width() / 500.0, view.viewport().height() / 500.0),
            rel_tol=1e-2,
        ),
        timeout=2000,
    )

    # Expanding viewport recalculates zoom upward
    view.resize(800, 600)
    qtbot.waitUntil(
        lambda: math.isclose(
            view.transform().m11(),
            min(view.viewport().width() / 500.0, view.viewport().height() / 500.0),
            rel_tol=1e-2,
        ),
        timeout=2000,
    )


def test_autofit_signals_and_action_toggle(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    with qtbot.waitSignal(view.autofitChanged) as blocker:
        view.autofit_action.trigger()
    assert blocker.args == [True]
    assert view.autofit is True
    assert view.autofit_action.isChecked() is True

    with qtbot.waitSignal(view.autofitChanged) as blocker:
        view.autofit_action.trigger()
    assert blocker.args == [False]
    assert view.autofit is False
    assert view.autofit_action.isChecked() is False


# Viewport Panning & Centering
def test_update_center_coordinates_and_reference_view(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(200, 200)
    view.show()

    pixmap = QPixmap(600, 600)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_zoom(2.0)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # Center on specific scene coordinates
    view.update_center((200.0, 200.0))
    center = view.mapToScene(view.viewport().rect().center())
    assert abs(center.x() - 200.0) <= 2.0
    assert abs(center.y() - 200.0) <= 2.0

    # Center using a reference GraphicsView
    view2 = GraphicsView()
    qtbot.addWidget(view2)
    view2.resize(200, 200)
    view2.show()
    view2.set_pixmap(pixmap)
    view2.set_zoom(2.0)
    qtbot.waitUntil(view2.isVisible, timeout=2000)

    view2.update_center(view)
    center2 = view2.mapToScene(view2.viewport().rect().center())
    assert abs(center2.x() - center.x()) <= 2.0
    assert abs(center2.y() - center.y()) <= 2.0


def test_panning_and_centering_bounds_clamping(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(200, 200)
    view.show()

    pixmap = QPixmap(300, 300)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_zoom(2.0)
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # Attempting to pan far beyond positive bounds clamps scrollbars and scene center
    view.update_center((999999.0, 999999.0))
    assert view.horizontalScrollBar().value() == view.horizontalScrollBar().maximum()
    assert view.verticalScrollBar().value() == view.verticalScrollBar().maximum()
    center_pos = view.mapToScene(view.viewport().rect().center())
    assert center_pos.x() < 1000.0
    assert center_pos.y() < 1000.0

    # Attempting to pan far beyond negative bounds clamps scrollbars and scene center
    view.update_center((-999999.0, -999999.0))
    assert view.horizontalScrollBar().value() == view.horizontalScrollBar().minimum()
    assert view.verticalScrollBar().value() == view.verticalScrollBar().minimum()
    center_neg = view.mapToScene(view.viewport().rect().center())
    assert center_neg.x() > -1000.0
    assert center_neg.y() > -1000.0


# RectSelectionOverlay & RectSelectionHandle
def test_rect_selection_handle_positions_and_priority() -> None:
    rect = QRect(10, 20, 100, 80)
    handles = RectSelectionHandle.compute_handle_pos(rect)

    assert len(handles) == 8
    assert handles[RectSelectionHandle.NORTH_WEST] == QPointF(10, 20)
    assert handles[RectSelectionHandle.NORTH_EAST] == QPointF(110, 20)
    assert handles[RectSelectionHandle.SOUTH_WEST] == QPointF(10, 100)
    assert handles[RectSelectionHandle.SOUTH_EAST] == QPointF(110, 100)
    assert handles[RectSelectionHandle.NORTH] == QPointF(60, 20)
    assert handles[RectSelectionHandle.SOUTH] == QPointF(60, 100)
    assert handles[RectSelectionHandle.WEST] == QPointF(10, 60)
    assert handles[RectSelectionHandle.EAST] == QPointF(110, 60)

    # Corner handles must precede edge handles in dict iteration for hit-test priority
    handle_order = list(handles.keys())
    assert handle_order[:4] == [
        RectSelectionHandle.NORTH_WEST,
        RectSelectionHandle.NORTH_EAST,
        RectSelectionHandle.SOUTH_WEST,
        RectSelectionHandle.SOUTH_EAST,
    ]


def test_rect_selection_handle_cursor_shapes() -> None:
    assert RectSelectionHandle.NORTH.cursor == Qt.CursorShape.SizeVerCursor
    assert RectSelectionHandle.SOUTH.cursor == Qt.CursorShape.SizeVerCursor
    assert RectSelectionHandle.EAST.cursor == Qt.CursorShape.SizeHorCursor
    assert RectSelectionHandle.WEST.cursor == Qt.CursorShape.SizeHorCursor
    assert RectSelectionHandle.NORTH_WEST.cursor == Qt.CursorShape.SizeFDiagCursor
    assert RectSelectionHandle.SOUTH_EAST.cursor == Qt.CursorShape.SizeFDiagCursor
    assert RectSelectionHandle.NORTH_EAST.cursor == Qt.CursorShape.SizeBDiagCursor
    assert RectSelectionHandle.SOUTH_WEST.cursor == Qt.CursorShape.SizeBDiagCursor


def test_rect_selection_overlay_properties_and_visibility(qtbot: QtBot) -> None:
    overlay = RectSelectionOverlay()

    # Shade opacity
    overlay.shade_opacity = 0.65
    assert math.isclose(overlay.shade_opacity, 0.65, rel_tol=1e-4)

    # Selection color via hex string and QColor
    overlay.selection_color = "#00ff00"
    assert overlay.selection_color == QColor(0, 255, 0)
    overlay.selection_color = QColor(255, 0, 0)
    assert overlay.selection_color == QColor(255, 0, 0)

    # Editable property
    overlay.editable = True
    assert overlay.editable is True
    overlay.editable = False
    assert overlay.editable is False

    # Visibility requires both non-empty image_rect and selection_rect
    assert overlay.isVisible() is False
    overlay.image_rect = QRectF(0, 0, 200, 100)
    assert overlay.isVisible() is False
    overlay.selection_rect = QRectF(10, 10, 50, 50)
    assert overlay.isVisible() is True
    overlay.selection_rect = QRectF()
    assert overlay.isVisible() is False

    # Bounding rect matches image_rect
    overlay.image_rect = QRectF(0, 0, 300, 200)
    assert overlay.boundingRect() == QRectF(0, 0, 300, 200)


def test_rect_selection_normalization_and_clipping(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(300, 200)
    view.show()

    pixmap = QPixmap(100, 80)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.rect_selection_enabled = True
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # Partial top-left overlap gets clipped to (0, 0)
    with qtbot.waitSignal(view.rectSelectionChanged) as blocker:
        view.set_rect_selection(QRect(-20, -10, 50, 40))
    assert blocker.args[0] == QRect(0, 0, 30, 30)  # pyright: ignore[reportOptionalSubscript]
    assert view.rect_selection == QRect(0, 0, 30, 30)

    # Partial bottom-right overlap gets clamped to image width/height
    with qtbot.waitSignal(view.rectSelectionFinished) as blocker:
        view.set_rect_selection(QRect(80, 60, 50, 50), finished=True)
    assert blocker.args[0] == QRect(80, 60, 20, 20)  # pyright: ignore[reportOptionalSubscript]
    assert view.rect_selection == QRect(80, 60, 20, 20)

    # Completely disjoint rect outside the image normalizes to empty
    view.set_rect_selection(QRect(200, 200, 50, 50))
    assert view.rect_selection.isEmpty()

    # Inverted / negative-dimension rect normalizes to empty
    view.set_rect_selection(QRect(10, 10, -5, 20))
    assert view.rect_selection.isEmpty()

    # Clearing rect selection
    view.set_rect_selection(QRect(10, 10, 20, 20))
    with qtbot.waitSignal(view.rectSelectionFinished) as blocker:
        view.clear_rect_selection()
    assert blocker.args[0] == QRect()  # pyright: ignore[reportOptionalSubscript]
    assert view.rect_selection.isEmpty()


def test_rect_selection_overlay_paint_execution(qtbot: QtBot) -> None:
    overlay = RectSelectionOverlay()
    overlay.image_rect = QRectF(0, 0, 100, 100)
    overlay.selection_rect = QRectF(20, 20, 40, 40)
    overlay.editable = True

    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.white)
    painter = QPainter(pixmap)
    option = QStyleOptionGraphicsItem()

    # Painting with handles enabled
    overlay.paint(painter, option)

    # Painting with handles disabled
    overlay.editable = False
    overlay.paint(painter, option)

    # Painting when selection is outside image bounds early-returns safely
    overlay.selection_rect = QRectF(200, 200, 50, 50)
    overlay.paint(painter, option)

    # Painting when image_rect is empty early-returns safely
    overlay.image_rect = QRectF()
    overlay.paint(painter, option)

    painter.end()


def test_rect_selection_move_drag(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.rect_selection_enabled = True
    view.set_rect_selection(QRect(10, 10, 30, 30))
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # Click inside selection interior (at 20, 20) and drag to 35, 35
    start = _view_pos_for_image_point(view, QPointF(20, 20)).toPoint()
    end = _view_pos_for_image_point(view, QPointF(35, 35)).toPoint()

    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(view.viewport(), pos=end)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)  # type: ignore[no-untyped-call]

    assert view.rect_selection == QRect(25, 25, 30, 30)


def test_rect_selection_resize_and_escape_cancel(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()

    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.rect_selection_enabled = True
    view.set_rect_selection(QRect(10, 10, 30, 30))
    qtbot.waitUntil(view.isVisible, timeout=2000)

    # SOUTH_EAST resize handle is at (40, 40)
    handle_pos = _view_pos_for_image_point(view, QPointF(40, 40)).toPoint()
    resized_pos = _view_pos_for_image_point(view, QPointF(55, 60)).toPoint()

    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=handle_pos)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(view.viewport(), pos=resized_pos)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=resized_pos)  # type: ignore[no-untyped-call]

    assert view.rect_selection == QRect(10, 10, 45, 50)

    # Starting a drag and pressing Escape restores the initial rect
    cancel_target = _view_pos_for_image_point(view, QPointF(80, 80)).toPoint()
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=resized_pos)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(view.viewport(), pos=cancel_target)  # type: ignore[no-untyped-call]
    qtbot.keyPress(view, Qt.Key.Key_Escape)

    assert view.rect_selection == QRect(10, 10, 45, 50)


def test_rect_selection_property_getters_and_scene_reset(qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()

    assert view.rect_selection_enabled is False
    view.rect_selection_enabled = True
    assert view.rect_selection_enabled is True
    # Setting same value exercises the early cursor update path
    view.rect_selection_enabled = True
    assert view.rect_selection_enabled is True
    view.rect_selection_enabled = False
    assert view.rect_selection_enabled is False

    # Overlay property getters
    overlay = RectSelectionOverlay()
    overlay.image_rect = QRectF(0, 0, 120, 80)
    assert overlay.image_rect == QRectF(0, 0, 120, 80)
    overlay.selection_rect = QRectF(10, 10, 40, 30)
    assert overlay.selection_rect == QRectF(10, 10, 40, 30)

    # Scene reset and clear
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_sar(2.0)
    view.sar_applied = True

    view.reset_scene()
    assert view.sar_applied is True
    assert view.pixmap_item.transform().m11() == 2.0

    view.clear_scene()
    assert len(view.graphics_scene.items()) == 0


# ViewState
def test_view_state_capture_and_restore(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()

    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_zoom(2.0)
    view.set_sar(1.5)
    view.sar_applied = True
    view.update_center((100.0, 100.0))
    qtbot.waitUntil(view.isVisible, timeout=2000)

    state = view.state
    assert isinstance(state, ViewState)
    assert math.isclose(state.zoom, 2.0)
    assert math.isclose(state.sar, 1.5)
    assert state.sar_applied is True
    assert state.autofit is False

    # Apply captured state to a separate view
    view2 = GraphicsView()
    qtbot.addWidget(view2)
    view2.resize(300, 300)
    view2.show()
    qtbot.waitUntil(view2.isVisible, timeout=2000)

    state.apply_pixmap(view2)
    state.apply_frozen_state(view2)

    assert view2.pixmap_item.pixmap().size() == view.pixmap_item.pixmap().size()
    assert math.isclose(view2.current_zoom, 2.0)
    assert math.isclose(view2.sar, 1.5)
    assert view2.sar_applied is True
    assert view2.slider.value() == state.slider_value


def test_view_state_apply_pixmap_with_rescaling(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    pixmap = QPixmap(100, 80)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)

    state = view.state

    view2 = GraphicsView()
    qtbot.addWidget(view2)
    state.apply_pixmap(view2, target_size=(50, 40))

    pm = view2.pixmap_item.pixmap()
    assert (pm.width(), pm.height()) == (50, 40)


def test_view_state_apply_frozen_state_with_sar_and_autofit(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    view = GraphicsView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()

    pixmap = QPixmap(150, 150)
    pixmap.fill(Qt.GlobalColor.white)
    view.set_pixmap(pixmap)
    view.set_autofit(True)
    view.set_sar(1.25)
    view.sar_applied = False
    qtbot.waitUntil(view.isVisible, timeout=2000)

    state = view.state
    assert state.autofit is True
    assert state.sar_applied is False

    view2 = GraphicsView()
    qtbot.addWidget(view2)
    view2.resize(300, 300)
    view2.show()
    qtbot.waitUntil(view2.isVisible, timeout=2000)

    state.apply_frozen_state(view2)
    assert view2.autofit is True
    assert view2.sar_applied is False
    assert view2.slider_container.isEnabled() is False
