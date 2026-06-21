import os

# Disable ANSI color escape codes in Numba compiler error/exception messages
os.environ["NUMBA_DISABLE_ERROR_MESSAGE_HIGHLIGHTING"] = "1"
os.environ["NUMBA_COLOR_SCHEME"] = "no_color"

from logging import INFO, getLogger
from typing import Any

import numba  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt

numba_logger = getLogger("numba")
numba_logger.setLevel(INFO)


@numba.njit(nogil=True, parallel=True, fastmath=True, cache=True)  # type: ignore[untyped-decorator]
def process_luma_numba(
    src: npt.NDArray[np.integer[Any] | np.floating[Any]],
    dst: npt.NDArray[np.uint8],
    bits: int,
    shift_in: int,
    use_sawtooth: bool,
    is_limited: bool,
) -> None:
    h, w = src.shape
    is_float = src.dtype.kind == "f"

    if is_float:
        max_val = 65535
        shift_out = 8
        if is_limited:
            scale = 56064.0 / 65535.0
            offset = 4096.0 / 65535.0
        else:
            scale = 1.0
            offset = 0.0
    else:
        max_val = (1 << bits) - 1
        shift_out = max(0, bits - 8)
        scale = 1.0
        offset = 0.0

    modulo_limit = max_val + 1

    if use_sawtooth:
        for y in numba.prange(h):
            for x in range(w):
                if is_float:
                    p_val = round((src[y, x] * scale + offset) * max_val)
                    p = 0 if p_val < 0 else min(p_val, max_val)
                else:
                    p = int(src[y, x])

                p_shifted = p << shift_in
                val = p_shifted & max_val
                dst[y, x] = val >> shift_out
    else:
        for y in numba.prange(h):
            for x in range(w):
                if is_float:
                    p_val = round((src[y, x] * scale + offset) * max_val)
                    p = 0 if p_val < 0 else min(p_val, max_val)
                else:
                    p = int(src[y, x])

                p_shifted = p << shift_in
                p_masked = p_shifted & max_val
                val = max_val - p_masked if (p_shifted & modulo_limit) else p_masked
                dst[y, x] = val >> shift_out
