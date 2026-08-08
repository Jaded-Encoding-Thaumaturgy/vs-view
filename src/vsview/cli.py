from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence

from vsview_cli import parse_args

from .env import getenv_bool, load_dotenv
from .logging import init_early_logging


def main(argv: Sequence[str] | None = None) -> None:
    init_early_logging()

    if not getenv_bool("VSVIEW_NO_DOTENV", False):
        load_dotenv()

    if argv is None:
        argv = sys.argv[1:]

    raw = parse_args(["vsview", *argv], shutil.get_terminal_size().columns)

    from .main import main as run_main

    return run_main(raw)
