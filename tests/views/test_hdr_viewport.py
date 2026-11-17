from __future__ import annotations

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QResizeEvent, QTransform
from pytestqt.qtbot import QtBot

from vsview.app.views.hdr import HDRImageProvider, HDRViewport

pytestmark = [pytest.mark.unit, pytest.mark.qt]


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


@pytest.mark.parametrize(
    ("attr", "qml_name", "value"),
    [
        ("image_width", "imageWidth", 1920),
        ("image_height", "imageHeight", 1080),
        ("image_x", "imageX", 45.5),
        ("image_y", "imageY", 120.25),
        ("image_scale_x", "imageScaleX", 2.5),
        ("image_scale_y", "imageScaleY", 3.0),
    ],
)
def test_hdr_viewport_properties_are_written_to_the_qml_root(
    qtbot: QtBot, attr: str, qml_name: str, value: float
) -> None:
    viewport = HDRViewport()
    qtbot.addWidget(viewport)

    setattr(viewport, attr, value)

    assert getattr(viewport, attr) == pytest.approx(value)
    assert viewport.root_obj
    assert viewport.root_obj.property(qml_name) == pytest.approx(value)


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
