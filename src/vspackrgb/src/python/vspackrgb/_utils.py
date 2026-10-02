from __future__ import annotations

import ctypes


def cast(value: int, ctype: type[ctypes._CT]) -> ctypes._Pointer[ctypes._CT]:
    return ctypes.cast(value, ctypes.POINTER(ctype))
