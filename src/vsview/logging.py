from collections.abc import Callable
from copy import copy
from logging import DEBUG, INFO, Formatter, LogRecord, captureWarnings, getLogger
from threading import main_thread
from typing import TypeGuard

from jetpytools import fallback
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

console = Console(stderr=True)
main_thread_name = main_thread().name


def _is_lambda(obj: object) -> TypeGuard[Callable[[], object]]:
    return callable(obj) and getattr(obj, "__name__", None) == "<lambda>"


class CustomHandler(RichHandler):
    def format(self, record: LogRecord) -> str:
        if record.args and record.name.startswith("vsview"):
            record.args = tuple(arg() if _is_lambda(arg) else arg for arg in record.args)
        return super().format(record)


class ThreadAwareFormatter(Formatter):
    def format(self, record: LogRecord) -> str:
        if record.threadName == main_thread_name:
            self._style._fmt = "{message}"
        else:
            self._style._fmt = "[{threadName}]: {message}"
        return super().format(record)


def setup_logging(
    level: int | None = None,
    vs_level: int | None = None,
    vsview_level: int | None = None,
    vsengine_level: int | None = INFO,
    capture_warnings: bool = True,
) -> None:
    # FIXME: Change that to INFO later
    level = fallback(level, DEBUG)

    # Root logger
    handler = CustomHandler(
        console=console,
        rich_tracebacks=True,
        log_time_format=lambda dt: Text("[{}.{:03d}]".format(dt.strftime("%H:%M:%S"), dt.microsecond // 1000)),
    )
    handler.setFormatter(Formatter("{name}: {message}", style="{"))

    root_logger = getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Set VS level. Handler is the same as the root one
    logger = getLogger("vapoursynth")
    logger.setLevel(fallback(vs_level, level))

    # Set custom formatter for vsview and vsengine
    handler = copy(handler)
    handler.setFormatter(ThreadAwareFormatter(style="{"))

    logger = getLogger("vsview")
    logger.setLevel(fallback(vsview_level, level))
    logger.addHandler(handler)
    logger.propagate = False

    logger = getLogger("vsengine")
    logger.setLevel(fallback(vsengine_level, level))
    logger.addHandler(handler)
    logger.propagate = False

    if capture_warnings:
        import warnings

        warnings.filterwarnings("always")
        captureWarnings(True)
