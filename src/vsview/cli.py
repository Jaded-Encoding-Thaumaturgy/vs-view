from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from vsview_cli import parse_args

from .env import getenv_bool, load_dotenv
from .logging import init_early_logging


def main(argv: Sequence[str] | None = None) -> None:
    init_early_logging()

    # Manually add scripts folder to the PATH when embebbed with PyApp
    if getenv_bool("PYAPP"):
        _populate_path()

    if not getenv_bool("VSVIEW_NO_DOTENV", False):
        load_dotenv()

    if argv is None:
        argv = sys.argv[1:]

    raw = parse_args(["vsview", *argv], shutil.get_terminal_size().columns)

    from .main import main as run_main

    return run_main(raw)


def _populate_path() -> None:
    py_bin = Path(sys.executable).parent
    path_parts = os.environ.get("PATH", "").split(os.pathsep)

    for folder in [py_bin, py_bin / "Scripts"] if sys.platform == "win32" else [py_bin]:
        if folder.is_dir() and str(folder.resolve()) not in path_parts:
            path_parts.insert(0, str(folder))

    os.environ["PATH"] = os.pathsep.join(path_parts)
