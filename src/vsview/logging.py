__lazy_modules__ = ["PySide6"]

import atexit
import faulthandler
import io
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from logging import (
    CRITICAL,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    FileHandler,
    Filter,
    Formatter,
    LogRecord,
    basicConfig,
    captureWarnings,
    getLogger,
)
from pathlib import Path
from threading import main_thread
from typing import TypeGuard, override

from jetpytools import fallback
from platformdirs import user_log_path
from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

console = Console(stderr=True)
main_thread_name = main_thread().name

IS_GUI_MODE = False
LOG_DIR = user_log_path("vsview", appauthor=False)
LOG_PATH = LOG_DIR / f"vsview_{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}.log"


def init_early_logging() -> None:
    global IS_GUI_MODE

    setup_basic_logging()
    logger = getLogger(__name__)

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


def _is_lambda(obj: object) -> TypeGuard[Callable[[], object]]:
    return callable(obj) and getattr(obj, "__name__", None) == "<lambda>"


def _format_lambda(record: LogRecord) -> LogRecord:
    if record.args and record.name.startswith("vsview"):
        record.args = tuple(arg() if _is_lambda(arg) else arg for arg in record.args)
    return record


def _qt_message_handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
    level_map = {
        QtMsgType.QtDebugMsg: DEBUG,
        QtMsgType.QtInfoMsg: INFO,
        QtMsgType.QtWarningMsg: WARNING,
        QtMsgType.QtCriticalMsg: ERROR,
        QtMsgType.QtFatalMsg: CRITICAL,
        QtMsgType.QtSystemMsg: CRITICAL,
    }

    category = context.category or "default"

    if not category.startswith("qt."):
        category = f"qt.{category}"

    level = level_map[mode]

    # Demote spammy FFmpeg version info to DEBUG
    if category == "qt.multimedia.ffmpeg" and level == INFO and "FFmpeg version" in message:
        level = DEBUG

    getLogger(category).log(level, message, stacklevel=2)


class EffectiveLevelFilter(Filter):
    @override
    def filter(self, record: LogRecord) -> bool:
        """Restores the level check for propagated records which Python skips by default."""
        return record.levelno >= getLogger(record.name).getEffectiveLevel()


class CustomHandler(RichHandler):
    @override
    def format(self, record: LogRecord) -> str:
        return super().format(_format_lambda(record))


class ThreadAwareFormatter(Formatter):
    @override
    def format(self, record: LogRecord) -> str:
        fmt = "{message}" if record.name.startswith("vsview") else "{name}: {message}"

        if record.threadName != main_thread_name:
            fmt = f"[{record.threadName}]: {fmt}"

        self._style._fmt = fmt
        return super().format(record)


class FileLogFormatter(Formatter):
    def __init__(self) -> None:
        super().__init__(fmt="[{asctime}] [{levelname:<7}] {name}: {message}", datefmt="%Y-%m-%d %H:%M:%S", style="{")

    @override
    def format(self, record: LogRecord) -> str:
        record = _format_lambda(record)

        if record.threadName != main_thread_name:
            fmt = f"[{{asctime}}] [{{levelname:<7}}] {{name}}: [{record.threadName}] {{message}}"
        else:
            fmt = "[{asctime}] [{levelname:<7}] {name}: {message}"

        self._style._fmt = fmt
        return super().format(record)


# One handler to rule them all
custom_handler = CustomHandler(
    console=console,
    rich_tracebacks=True,
    log_time_format=lambda dt: Text("[{}.{:03d}]".format(dt.strftime("%H:%M:%S"), dt.microsecond // 1000)),
)
custom_handler.setFormatter(ThreadAwareFormatter(style="{"))
custom_handler.addFilter(EffectiveLevelFilter())


def setup_basic_logging() -> None:
    basicConfig(handlers=[custom_handler], level=INFO)


def setup_logging(
    level: int | None = None,
    vs_level: int | None = None,
    vsview_level: int | None = None,
    vsengine_level: int | None = None,
    qt_level: int | None = None,
    log_file: Path | None = None,
    is_gui_mode: bool = False,
    capture_warnings: bool = True,
) -> None:
    qInstallMessageHandler(_qt_message_handler)

    console_level = fallback(level, INFO)
    custom_handler.setLevel(console_level)

    root_logger = getLogger()

    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
        h.close()

    root_level = min(DEBUG, console_level)
    root_logger.setLevel(root_level)

    # In GUI mode (pythonw), stderr points to log file
    # We can omit console handler to prevent duplicate Rich logs
    if not is_gui_mode:
        root_logger.addHandler(custom_handler)

    file_handler: FileHandler | None = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(DEBUG)
        file_handler.setFormatter(FileLogFormatter())
        root_logger.addHandler(file_handler)

    # Set levels for specialized loggers, they will all propagate to the root handler
    getLogger("vapoursynth").setLevel(fallback(vs_level, root_level))
    getLogger("vsview").setLevel(fallback(vsview_level, root_level))
    getLogger("vsengine").setLevel(fallback(vsengine_level, INFO))
    getLogger("qt").setLevel(fallback(qt_level, root_level))

    if capture_warnings:
        captureWarnings(True)


def get_console_level() -> int:
    """Return the currently configured log level for console output (accounts for --verbose)."""
    return custom_handler.level
