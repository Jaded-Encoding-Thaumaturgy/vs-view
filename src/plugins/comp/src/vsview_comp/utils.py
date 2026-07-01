from __future__ import annotations

import importlib.metadata
import itertools
import math
from bisect import bisect_right
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from functools import cache
from logging import getLogger
from types import TracebackType
from typing import override

import niquests
from jetpytools import clamp
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


class UploadError(Exception): ...


def get_probability_cdf(start_frame: int, end_frame: int, curve_points: Sequence[QPointF]) -> tuple[list[float], float]:
    """
    Computes the Cumulative Distribution Function (CDF) and total weight based on a probability curve.
    """
    num_frames = end_frame - start_frame + 1

    if num_frames <= 1:
        weights = [1.0]
    else:
        weights = [
            get_temporal_weight((f - start_frame) / (end_frame - start_frame), curve_points)
            for f in range(start_frame, end_frame + 1)
        ]

    return build_cdf(weights)


def build_cdf(weights: Sequence[float]) -> tuple[list[float], float]:
    """
    Accumulates weights into a CDF. Falls back to uniform distribution if total weight is near-zero.
    """
    cdf = list(itertools.accumulate(weights))
    total_weight = cdf[-1] if cdf else 0.0

    if total_weight <= 1e-6:
        cdf = list(itertools.accumulate([1.0] * len(weights)))
        total_weight = len(weights)

    return cdf, total_weight


def get_temporal_weight(x: float, curve_points: Sequence[QPointF]) -> float:
    """
    Interpolates the temporal probability weight for a normalized position x in [0.0, 1.0].
    """
    if not curve_points:
        return 1.0

    if len(curve_points) == 1:
        return curve_points[0].y()

    x = clamp(x, 0.0, 1.0)
    idx = bisect_right(curve_points, x, key=lambda pt: pt.x())

    # Ensure idx is mapped to a valid interval segment [idx-1, idx]
    idx = clamp(idx, 1, len(curve_points) - 1)
    l, r = curve_points[idx - 1], curve_points[idx]  # noqa: E741

    # Linear interpolation
    w = l.y() if (dx := r.x() - l.x()) == 0.0 else l.y() + (r.y() - l.y()) * (x - l.x()) / dx

    return max(0.0, w)


def asymmetric_gaussian(x: float, mu: float, sigma_left: float, sigma_right: float) -> float:
    """
    Computes weight using an asymmetric Gaussian curve.
    """
    sigma = sigma_left if x < mu else sigma_right

    if sigma <= 0.0:
        return 1.0 if x == mu else 0.0

    return math.exp(-((x - mu) ** 2) / (2 * (sigma**2)))
