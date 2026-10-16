from __future__ import annotations

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QResizeEvent, QTransform
from pytestqt.qtbot import QtBot

from vsview.app.views.hdr import HDRImageProvider, HDRViewport, QMLProperty

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def test_qml_property_descriptor_class_access() -> None:
    """Accessing QMLProperty on the class returns the descriptor itself."""
    assert isinstance(HDRViewport.image_width, QMLProperty)
    assert HDRViewport.image_width.qml_name == "imageWidth"
    assert HDRViewport.image_height.qml_name == "imageHeight"  # pyright: ignore[reportAttributeAccessIssue]
    assert HDRViewport.image_scale_x.qml_name == "imageScaleX"  # pyright: ignore[reportAttributeAccessIssue]
    assert HDRViewport.image_scale_y.qml_name == "imageScaleY"  # pyright: ignore[reportAttributeAccessIssue]
    assert HDRViewport.image_x.qml_name == "imageX"  # pyright: ignore[reportAttributeAccessIssue]
    assert HDRViewport.image_y.qml_name == "imageY"  # pyright: ignore[reportAttributeAccessIssue]


def test_hdr_image_provider_initial_state_and_request_image() -> None:
    """HDRImageProvider initializes with a 1x1 RGBX16FPx4 frame and returns it on request."""
    provider = HDRImageProvider()
    assert not provider.image.isNull()
    assert provider.image.width() == 1
    assert provider.image.height() == 1
    assert provider.image.format() == QImage.Format.Format_RGBX16FPx4

    requested = provider.requestImage("frame", QSize(), QSize())
    assert requested == provider.image

    custom_image = QImage(128, 64, QImage.Format.Format_RGBX16FPx4)
    custom_image.fill(Qt.GlobalColor.white)
    provider.image = custom_image

    assert provider.requestImage("frame_custom", QSize(), QSize()) == custom_image


def test_hdr_viewport_initialization(qtbot: QtBot) -> None:
    """HDRViewport instantiates with transparent background and sets up QML view root object."""
    viewport = HDRViewport()
    qtbot.addWidget(viewport)

    assert viewport.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    assert viewport.root_obj is not None

    # Initial default values from QML
    assert viewport.image_width == 0
    assert viewport.image_height == 0
    assert viewport.image_scale_x == pytest.approx(1.0)
    assert viewport.image_scale_y == pytest.approx(1.0)
    assert viewport.image_x == pytest.approx(0.0)
    assert viewport.image_y == pytest.approx(0.0)


def test_hdr_viewport_property_sync(qtbot: QtBot) -> None:
    """Setting and getting HDRViewport QMLProperty descriptors correctly synchronizes with QML root object."""
    viewport = HDRViewport()
    qtbot.addWidget(viewport)

    viewport.image_width = 1920
    assert viewport.image_width == 1920
    assert viewport.root_obj.property("imageWidth") == 1920

    viewport.image_height = 1080
    assert viewport.image_height == 1080
    assert viewport.root_obj.property("imageHeight") == 1080

    viewport.image_x = 45.5
    assert viewport.image_x == pytest.approx(45.5)
    assert viewport.root_obj.property("imageX") == pytest.approx(45.5)

    viewport.image_y = 120.25
    assert viewport.image_y == pytest.approx(120.25)
    assert viewport.root_obj.property("imageY") == pytest.approx(120.25)

    viewport.image_scale_x = 2.5
    assert viewport.image_scale_x == pytest.approx(2.5)
    assert viewport.root_obj.property("imageScaleX") == pytest.approx(2.5)

    viewport.image_scale_y = 3.0
    assert viewport.image_scale_y == pytest.approx(3.0)
    assert viewport.root_obj.property("imageScaleY") == pytest.approx(3.0)


def test_hdr_viewport_set_image_null_is_safe_noop(qtbot: QtBot) -> None:
    """Passing a null QImage to set_image performs no operation and preserves previous state."""
    viewport = HDRViewport()
    qtbot.addWidget(viewport)

    viewport.image_width = 800
    viewport.image_height = 600

    null_image = QImage()
    assert null_image.isNull()

    viewport.set_image(null_image)

    assert viewport.image_width == 800
    assert viewport.image_height == 600


def test_hdr_viewport_set_video_transform(qtbot: QtBot) -> None:
    """set_video_transform maps translation and scaling factors directly to QML properties."""
    viewport = HDRViewport()
    qtbot.addWidget(viewport)

    transform = QTransform()
    transform.translate(15.75, 42.25)
    transform.scale(1.75, 2.25)

    viewport.set_video_transform(transform)

    assert viewport.image_x == pytest.approx(15.75)
    assert viewport.image_y == pytest.approx(42.25)
    assert viewport.image_scale_x == pytest.approx(1.75)
    assert viewport.image_scale_y == pytest.approx(2.25)


def test_hdr_viewport_resize_event(qtbot: QtBot) -> None:
    """resizeEvent resizes the underlying QQuickView container."""
    viewport = HDRViewport()
    qtbot.addWidget(viewport)
    viewport.show()

    viewport.resize(500, 400)
    event = QResizeEvent(QSize(500, 400), QSize(300, 200))
    viewport.resizeEvent(event)

    assert viewport.quick_view.size() == QSize(500, 400)
