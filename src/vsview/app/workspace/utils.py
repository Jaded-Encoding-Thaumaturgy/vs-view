from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import sys
import sysconfig
import textwrap
from collections import UserDict
from collections.abc import Iterable
from functools import cache
from importlib.util import find_spec
from logging import getLogger
from pathlib import Path
from typing import Any, override

import vapoursynth

logger = getLogger(__name__)

EXCLUDED_PREFIXES = frozenset(
    {
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path(sys.exec_prefix).resolve(),
        Path(sys.base_exec_prefix).resolve(),
        Path(sysconfig.get_path("purelib")).resolve(),
        Path(sysconfig.get_path("platlib")).resolve(),
    }
)


def _get_installed_top_levels() -> set[str]:
    installed = set(importlib.metadata.packages_distributions().keys())

    for dist in importlib.metadata.distributions():
        if (name := dist.metadata.get("Name")) is None:
            logger.warning("Malformed distribution %s", dist)
            continue
        installed.add(name.replace("-", "_"))

    return installed


def find_local_packages() -> set[str]:
    """Find top-level package names of locally-available (non-installed) modules."""
    local_packages = set[str]()
    installed = _get_installed_top_levels()

    for module in sys.modules.values():
        if not (mod_file := getattr(module, "__file__", None)):
            continue

        top_level = module.__name__.split(".")[0]

        if top_level in installed:
            continue

        mod_path = Path(mod_file).resolve()

        if any(mod_path.is_relative_to(p) for p in EXCLUDED_PREFIXES) or not mod_path.is_file():
            continue

        local_packages.add(top_level)

    return local_packages


def evict_packages(packages: Iterable[str]) -> None:
    """Evict all submodules of the given top-level packages from ``sys.modules``."""

    for package in sorted(packages):
        submodules = sorted(k for k in sys.modules if k == package or k.startswith(f"{package}."))

        for mod_name in reversed(submodules):
            del sys.modules[mod_name]

        logger.debug('Evicted package: "%s"', package)


@cache
def get_default_script() -> str:
    code = ""
    if find_spec("vstools"):
        code += "from vstools import core, initialize_clip, vs\n"
    else:
        code += "import vapoursynth as vs\n"
        code += "\n"
        code += "core = vs.core\n"

    code += "from vsview import set_output\n"
    code += "\n"

    code += "clip = core.std.BlankClip()\n"

    if find_spec("vstools"):
        code += "clip = initialize_clip(clip, None)\n"
    else:
        code += textwrap.dedent("""
        clip = core.std.SetFrameProps(
            clip,
            _Matrix=vs.MATRIX_RGB,
            _Primaries=vs.PRIMARIES_BT709,
            _Transfer=vs.TRANSFER_IEC_61966_2_1,
        )\n""")

    code += "set_output(clip)\n"

    return code


class CodeContent:
    __slots__ = ("code", "filename")

    def __init__(self, code: str, filename: str) -> None:
        self.code = code
        self.filename = filename

    def splitlines(self, keepends: bool = False) -> list[str]:
        return self.code.splitlines(keepends)

    def __len__(self) -> int:
        return len(self.code.splitlines(keepends=False))

    @override
    def __str__(self) -> str:
        return self.code

    @override
    def __repr__(self) -> str:
        return self.filename


class State(UserDict[Any, Any]):
    @override
    def __setitem__(self, key: Any, item: Any) -> None:
        if is_from_vs_module(key) or is_from_vs_module(item):
            raise ValueError(f"Cannot store VapourSynth objects in persistent state for key {key!r}.")

        return super().__setitem__(key, item)


def is_from_vs_module(obj: Any) -> bool:
    """Returns true if the obj is a VapourSynth object or was defined in the vapoursynth module."""
    if isinstance(
        obj,
        (
            vapoursynth.VideoNode,
            vapoursynth.AudioNode,
            vapoursynth.VideoFrame,
            vapoursynth.AudioFrame,
            vapoursynth.Core,
            vapoursynth.Environment,
            vapoursynth.Plugin,
            vapoursynth.Function,
            vapoursynth.RawNode,
        ),
    ):
        return True

    if (mod_name := getattr(obj, "__module__", None)) and (mod := sys.modules.get(mod_name)) and mod is vapoursynth:
        return True

    return bool((mod := inspect.getmodule(type(obj))) and mod is vapoursynth)
