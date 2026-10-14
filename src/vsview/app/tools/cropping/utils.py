from __future__ import annotations

import contextlib
from typing import NamedTuple, Self

from jetpytools import clamp
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLabel, QWidget
from vapoursynth import VideoNode

from vsview.assets.utils import get_monospace_font


class CropValues(NamedTuple):
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_rect(cls, rect: QRect, image_size: QSize) -> CropValues:
        image_w = image_size.width()
        image_h = image_size.height()

        width = rect.width()
        height = rect.height()

        return cls(
            left=rect.x(),
            top=rect.y(),
            right=image_w - (rect.x() + width),
            bottom=image_h - (rect.y() + height),
            width=width,
            height=height,
        )


class CommandLabel(QLabel):
    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent, wordWrap=True, textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.original_text = text
        self.setFont(get_monospace_font(10))
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.1); padding: 4px;")

    def format(self, *args: object, **kwargs: object) -> None:
        self.reset_text()
        with contextlib.suppress(KeyError, ValueError, IndexError):
            self.setText(self.text().format(*args, **kwargs))

    def reset_text(self) -> None:
        self.setText(self.original_text)


class CustomSize(QSize):
    @classmethod
    def from_clip(cls, clip: VideoNode) -> Self:
        return cls(clip.width, clip.height)


class CustomRect(QRect):
    def clamp(self, size: QSize) -> None:
        image_w = size.width()
        image_h = size.height()

        x0 = clamp(self.x(), 0, image_w)
        y0 = clamp(self.y(), 0, image_h)
        x1 = clamp(self.x() + self.width(), 0, image_w)
        y1 = clamp(self.y() + self.height(), 0, image_h)

        if x1 <= x0 or y1 <= y0:
            return

        self.setLeft(x0)
        self.setTop(y0)
        self.setWidth(x1 - x0)
        self.setHeight(y1 - y0)

    def sanitize(self, size: QSize, mod: int) -> None:
        mod = max(mod, 1)

        image_w = size.width()
        image_h = size.height()

        x0 = self.x() - self.x() % mod
        y0 = self.y() - self.y() % mod
        x1 = ((self.x() + self.width() + mod - 1) // mod) * mod
        y1 = ((self.y() + self.height() + mod - 1) // mod) * mod

        x0 = clamp(x0, 0, image_w)
        y0 = clamp(y0, 0, image_h)
        x1 = clamp(x1, 0, image_w)
        y1 = clamp(y1, 0, image_h)

        if x1 <= x0 or y1 <= y0:
            self.setRect(0, 0, 0, 0)
            return

        self.setLeft(x0)
        self.setTop(y0)
        self.setWidth(x1 - x0)
        self.setHeight(y1 - y0)

    @classmethod
    def from_crop(cls, left: int, top: int, right: int, bottom: int, size: QSize, mod: int) -> Self:
        image_w = size.width()
        image_h = size.height()

        x0 = clamp(left, 0, image_w)
        y0 = clamp(top, 0, image_h)
        x1 = clamp(image_w - right, 0, image_w)
        y1 = clamp(image_h - bottom, 0, image_h)

        rect = cls(x0, y0, max(0, x1 - x0), max(0, y1 - y0))
        rect.sanitize(size, mod)
        return rect
