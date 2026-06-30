from __future__ import annotations

import importlib.metadata
import itertools
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from functools import cache
from logging import getLogger
from types import TracebackType
from typing import override

import niquests
from PySide6.QtCore import QPointF

logger = getLogger(__name__)


@cache
def get_slowpics_headers() -> dict[str, str]:
    version = importlib.metadata.version("vsview-comp")
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://slow.pics",
        "Referer": "https://slow.pics/comparison",
        "User-Agent": f"vs-view (https://github.com/Jaded-Encoding-Thaumaturgy/vs-view {version})",
    }


class LogNiquestsErrors(AbstractContextManager[None], AbstractAsyncContextManager[None]):
    def __init__(self, ctx_message: str) -> None:
        self.ctx_message = ctx_message

    @override
    def __enter__(self) -> None:
        return None

    @override
    def __exit__(
        self,
        exc_t: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        if isinstance(exc_val, niquests.HTTPError):
            logger.error("%s failed: %s", self.ctx_message, exc_val, stacklevel=4)
            logger.debug("Full traceback", exc_info=exc_val, stacklevel=4)
            return True
        return None

    @override
    async def __aenter__(self) -> None:
        return None

    @override
    async def __aexit__(
        self,
        exc_t: type[BaseException] | None,
        exc_val: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return self.__exit__(exc_t, exc_val, tb)


class UploadError(Exception):
    pass


def get_probability_cdf(start_frame: int, end_frame: int, curve_points: Sequence[QPointF]) -> tuple[list[float], float]:
    """
    Computes the Cumulative Distribution Function (CDF) and total weight based on a probability curve.
    """
    num_frames = end_frame - start_frame + 1

    if num_frames <= 1:
        weights = [1.0]
    else:
        pt_idx = 0
        weights = list[float]()

        for f in range(start_frame, end_frame + 1):
            x = (f - start_frame) / (end_frame - start_frame)

            while pt_idx < len(curve_points) - 2 and x > curve_points[pt_idx + 1].x():
                pt_idx += 1

            l, r = curve_points[pt_idx], curve_points[pt_idx + 1]  # noqa: E741
            w = l.y() if l.x() == r.x() else l.y() + (r.y() - l.y()) * (x - l.x()) / (r.x() - l.x())
            weights.append(max(0.0, w))

    cdf = list(itertools.accumulate(weights))
    total_weight = cdf[-1] if cdf else 0.0

    # Fallback for ultra-low total weight
    if total_weight <= 1e-6:
        cdf = list(itertools.accumulate([1.0] * num_frames))
        total_weight = float(num_frames)

    return cdf, total_weight
