from collections.abc import Generator
from random import random

from PySide6.QtGui import QColor


def color_generator(hue: float | None = None) -> Generator[QColor, QColor | None, None]:
    if hue is None:
        hue = random()

    golden_ratio_conjugate = 0.618033988749895

    while True:
        sent = yield QColor.fromHsvF(hue, 0.5, 0.9)
        hue = (sent.hueF() if sent is not None else hue + golden_ratio_conjugate) % 1.0
