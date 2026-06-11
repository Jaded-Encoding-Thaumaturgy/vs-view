from __future__ import annotations

from logging import getLogger
from typing import Any, Self, overload, override

from jetpytools import SPath
from PySide6.QtCore import QMetaObject, QSize, Qt, QUrl
from PySide6.QtGui import QColorSpace, QImage, QResizeEvent, QTransform
from PySide6.QtQuick import QQuickImageProvider, QQuickView
from PySide6.QtWidgets import QVBoxLayout, QWidget

logger = getLogger(__name__)


class QMLProperty[T]:
    def __init__(self, qml_name: str) -> None:
        self.qml_name = qml_name

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...
    @overload
    def __get__(self, instance: HDRViewport, owner: type | None = None) -> T: ...
    def __get__(self, instance: HDRViewport | None, owner: type | None = None) -> Any:
        return self if instance is None else instance.root_obj.property(self.qml_name)

    def __set__(self, instance: HDRViewport, value: T) -> None:
        instance.root_obj.setProperty(self.qml_name, value)


class HDRImageProvider(QQuickImageProvider):
    """Bridge between Python QImage (HDR) and QML."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self.image = QImage(1, 1, QImage.Format.Format_RGBA16FPx4)
        self.image.fill(Qt.GlobalColor.transparent)

    @override
    def requestImage(self, id: str, size: QSize, requestedSize: QSize) -> QImage:
        return self.image


class HDRViewport(QWidget):
    """HDR-capable background viewport."""

    QML_PATH = SPath(__file__).parent / "hdr.qml"

    # Map Python attributes to QML properties via descriptor
    image_width = QMLProperty[int]("imageWidth")
    image_height = QMLProperty[int]("imageHeight")
    image_x = QMLProperty[float]("imageX")
    image_y = QMLProperty[float]("imageY")
    image_scale_x = QMLProperty[float]("imageScaleX")
    image_scale_y = QMLProperty[float]("imageScaleY")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self._provider = HDRImageProvider()

        self._view = QQuickView()
        self._view.setFlags(self._view.flags() | Qt.WindowType.WindowTransparentForInput)
        self._view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._view.engine().addImageProvider("hdr", self._provider)
        self._view.setColor(Qt.GlobalColor.transparent)
        self._view.setSource(QUrl.fromLocalFile(self.QML_PATH.resolve().to_str()))

        if (status := self._view.status()) != QQuickView.Status.Ready:
            logger.warning("QML Status: %s", status)
            for error in self._view.errors():
                logger.error("QML Error: %s", error.toString())

        self.root_obj = self._view.rootObject()

        self._container = QWidget.createWindowContainer(self._view, self)
        self._container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._container)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._view.resize(self.size())

    def set_image(self, frame: QImage) -> None:
        if frame.isNull():
            return

        frame.setColorSpace(QColorSpace.NamedColorSpace.Bt2100Pq)

        if (target_format := frame.format()) not in (QImage.Format.Format_RGBA16FPx4, QImage.Format.Format_RGBA32FPx4):
            target_format = QImage.Format.Format_RGBA16FPx4
            logger.debug("Target format is %s. Implicit conversion to RGBA16FPx4 will be performed", target_format)

        sc_rgb_cs = QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear)
        hdr_image = frame.convertToFormat(target_format)
        hdr_image = hdr_image.convertedToColorSpace(sc_rgb_cs)

        self._provider.image = hdr_image

        # Sync metadata to QML (dimensions and transform)
        self.image_width = frame.width()
        self.image_height = frame.height()

        # Trigger refresh in QML
        QMetaObject.invokeMethod(self.root_obj, "refresh")

    def set_video_transform(self, transform: QTransform) -> None:
        self.image_x = transform.dx()
        self.image_y = transform.dy()
        self.image_scale_x = transform.m11()
        self.image_scale_y = transform.m22()
