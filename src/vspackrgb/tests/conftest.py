from importlib import import_module
from importlib.util import find_spec
from typing import Protocol, cast

import pytest


class BackendModule(Protocol):
    def pack_bgra_8bit(
        self,
        b_ptr: int,
        g_ptr: int,
        r_ptr: int,
        a_ptr: int | None,
        width: int,
        height: int,
        src_stride: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...

    def pack_rgb30_10bit(
        self,
        r_ptr: int,
        g_ptr: int,
        b_ptr: int,
        a_ptr: int | None,
        width: int,
        height: int,
        samples_per_row: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...

    def pack_rgba64_16bit(
        self,
        r_ptr: int,
        g_ptr: int,
        b_ptr: int,
        a_ptr: int | None,
        width: int,
        height: int,
        samples_per_row: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...

    def pack_rgba16f_16bit(
        self,
        r_ptr: int,
        g_ptr: int,
        b_ptr: int,
        a_ptr: int | None,
        width: int,
        height: int,
        samples_per_row: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...

    def pack_rgba32f_32bit(
        self,
        r_ptr: int,
        g_ptr: int,
        b_ptr: int,
        a_ptr: int | None,
        width: int,
        height: int,
        samples_per_row: int,
        dest_ptr: int,
        dest_stride: int,
    ) -> None: ...


BACKENDS = [
    "python",
    "rust",
    pytest.param("numpy", marks=pytest.mark.skipif(not find_spec("numpy"), reason="NumPy not installed")),
    pytest.param("numba", marks=pytest.mark.skipif(not find_spec("numba"), reason="Numba not installed")),
]


def get_backend_module(backend_name: str) -> BackendModule:
    match backend_name:
        case "python":
            mod = import_module("vspackrgb.python")
        case "numpy":
            mod = import_module("vspackrgb.numpy")
        case "rust":
            mod = import_module("vspackrgb.rust")
        case "numba":
            mod = import_module("vspackrgb.numba")
        case _:
            raise ValueError(f"Unknown backend: {backend_name}")
    return cast(BackendModule, mod)


@pytest.fixture(params=BACKENDS)
def backend_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def backend(backend_name: str) -> BackendModule:
    return get_backend_module(backend_name)
