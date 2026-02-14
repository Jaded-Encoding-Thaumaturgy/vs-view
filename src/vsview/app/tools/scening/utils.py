from __future__ import annotations

import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from random import random
from types import MethodType
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor

if TYPE_CHECKING:
    from .api import Parser


class ColorGenerator(Generator[QColor, QColor | None]):
    def __init__(self, hue: float | None = None) -> None:
        self._hue = hue if hue is not None else random()
        self._lock = threading.Lock()
        self._golden_ratio_conjugate = 0.618033988749895

    def send(self, value: QColor | None) -> QColor:
        with self._lock:
            if value is not None:
                self._hue = value.hueF()

            res = QColor.fromHsvF(self._hue, 0.5, 0.55)
            self._hue = (self._hue + self._golden_ratio_conjugate) % 1.0
            return res

    def throw(self, exc: BaseException, /, *_: Any) -> QColor:  # type: ignore[override]
        raise exc


@contextmanager
def monkey_patch_parser(parser: Parser, color_gen: Generator[QColor, QColor | None]) -> Iterator[None]:
    setattr(parser, "get_color", MethodType(lambda _: next(color_gen), parser))

    try:
        yield
    finally:
        delattr(parser, "get_color")
