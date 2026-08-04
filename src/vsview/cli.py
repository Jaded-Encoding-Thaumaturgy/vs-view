from __future__ import annotations

import atexit
import faulthandler
import io
import shutil
import sys
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from logging import getLogger

from platformdirs import user_log_path
from vsview_cli import parse_args

from .env import getenv_bool, load_dotenv
from .logging import console, setup_basic_logging

setup_basic_logging()
logger = getLogger(__name__)

IS_GUI_MODE = False
LOG_DIR = user_log_path("vsview", appauthor=False)
LOG_PATH = LOG_DIR / f"vsview_{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}.log"
if LOG_DIR.exists():
    existing_logs = sorted(LOG_DIR.glob("vsview_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_log in existing_logs[9:]:
        try:
            old_log.unlink()
        except OSError:
            logger.exception("Couldn't remove %s", old_log.resolve())


# Enable faulthandler to get stack traces on segfaults
for stream in (console.file, sys.stderr, sys.__stderr__):
    if not stream:
        continue

    with suppress(AttributeError, OSError, RuntimeError, ValueError, io.UnsupportedOperation):
        stream.fileno()
        faulthandler.enable(file=stream)
        break
else:
    # Running without console handle (pythonw / PYAPP); redirect streams to log file for plugins & faulthandler
    IS_GUI_MODE = True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_buffer = LOG_PATH.open("a", encoding="utf-8")
    sys.stdout = log_buffer
    sys.stderr = log_buffer
    sys.stderr.write(f"=== VSView {datetime.now(UTC).isoformat()} ===\n")
    sys.stderr.flush()
    faulthandler.enable(file=sys.stderr)
    atexit.register(log_buffer.close)
    console.width = 200


def main(argv: Sequence[str] | None = None) -> None:
    if not getenv_bool("VSVIEW_NO_DOTENV", False):
        load_dotenv()

    if argv is None:
        argv = sys.argv[1:]

    raw = parse_args(["vsview", *argv], shutil.get_terminal_size().columns)

    from .main import main

    return main(raw)
