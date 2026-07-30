from __future__ import annotations

import importlib
import importlib.metadata
import sys
import sysconfig
import textwrap
from collections.abc import Iterable
from functools import cache
from importlib.util import find_spec
from logging import getLogger
from pathlib import Path
from typing import override

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
