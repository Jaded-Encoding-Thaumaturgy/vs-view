from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from io import StringIO
from logging import Handler, Logger, LogRecord
from typing import Literal, TextIO, override

from jetpytools import CustomRuntimeError, Singleton, inject_self
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

from vsview.api import QObjectSet

from .web import MonacoBridge

IN_LOGGING = threading.local()
EXECUTION_BRIDGE = ContextVar[MonacoBridge | None]("EXECUTION_BRIDGE", default=None)


class EditorConsoleHandler(RichHandler):
    def __init__(self, dispatch: Callable[[Literal["stderr", "stdout"], str], None]) -> None:
        self.dispatch = dispatch
        super().__init__(
            console=Console(file=StringIO(), record=True),
            rich_tracebacks=True,
            log_time_format=lambda dt: Text("[{}.{:03d}]".format(dt.strftime("%H:%M:%S"), dt.microsecond // 1000)),
        )

    @override
    def emit(self, record: LogRecord) -> None:
        IN_LOGGING.depth = getattr(IN_LOGGING, "depth", 0) + 1
        try:
            self.console.width = GlobalConsoleHub.target_width
            super().emit(record)
            self.dispatch("stderr", self.console.export_text(clear=True, styles=True))
        finally:
            IN_LOGGING.depth -= 1


class ConsoleStreamRedirector:
    def __init__(self, name: Literal["stdout", "stderr"]) -> None:
        self.name = name
        self._original_stream: TextIO = getattr(sys, self.name)
        self._installed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._original_stream, name)

    def install(self) -> None:
        if self._installed:
            raise CustomRuntimeError("This ConsoleStreamRedirector is already installed", self.install)

        setattr(sys, self.name, self)
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            raise CustomRuntimeError("This ConsoleStreamRedirector is already uninstalled", self.uninstall)

        setattr(sys, self.name, self._original_stream)
        self._installed = False

    def write(self, text: str) -> int:
        res = self._original_stream.write(text)
        # Suppress dispatching when text comes from an active logging emit() call
        if text and getattr(IN_LOGGING, "depth", 0) == 0:
            GlobalConsoleHub.dispatch({"stream": self.name, "text": text})
        return res

    def isatty(self) -> bool:
        return getattr(self._original_stream, "isatty", lambda: False)()


class GlobalConsoleHub(Singleton):
    def __init__(self) -> None:
        self._bridges = QObjectSet[MonacoBridge]()
        self._bridge_widths = dict[MonacoBridge, int]()
        self._stdout_redirector = ConsoleStreamRedirector("stdout")
        self._stderr_redirector = ConsoleStreamRedirector("stderr")
        self._log_handler: EditorConsoleHandler | None = None
        self._orig_handler_handle: Callable[[Handler, LogRecord], int | None] | None = None
        self._installed = False

    @inject_self.property
    def target_width(self) -> int:
        if (target_bridge := EXECUTION_BRIDGE.get()) in self._bridge_widths:
            return self._bridge_widths[target_bridge]

        for bridge in self._bridges:
            if bridge in self._bridge_widths:
                return self._bridge_widths[bridge]

        if self._log_handler:
            return self._log_handler.console.width

        return 80

    @inject_self
    def register(self, bridge: MonacoBridge) -> None:
        self._bridges.add(bridge)
        if not self._installed:
            self._install_global_hooks()

    @inject_self
    def unregister(self, bridge: MonacoBridge) -> None:
        self._bridges.discard(bridge)
        self._bridge_widths.pop(bridge, None)
        if not self._bridges and self._installed:
            self._uninstall_global_hooks()

    @inject_self
    def set_width(self, bridge: MonacoBridge, width: int) -> None:
        self._bridge_widths[bridge] = width
        target_bridge = EXECUTION_BRIDGE.get()
        if self._log_handler and (target_bridge == bridge or (target_bridge is None and bridge in self._bridges)):
            self._log_handler.console.width = width

    @inject_self
    @contextmanager
    def bind_execution(self, bridge: MonacoBridge) -> Generator[None]:
        token = EXECUTION_BRIDGE.set(bridge)
        try:
            yield
        finally:
            EXECUTION_BRIDGE.reset(token)

    @inject_self
    def dispatch(self, payload: dict[str, str], target_bridge: MonacoBridge | None = None) -> None:
        dest_bridge = target_bridge or EXECUTION_BRIDGE.get()
        if dest_bridge is not None:
            dest_bridge.dispatch("console.append", payload)
        else:
            for bridge in self._bridges:
                bridge.dispatch("console.append", payload)

    def _install_global_hooks(self) -> None:
        self._stdout_redirector.install()
        self._stderr_redirector.install()

        self._orig_handler_handle = _orig_handler_handle = Handler.handle

        def _protected_handle(handler_self: Handler, record: LogRecord) -> bool:
            nonlocal _orig_handler_handle
            IN_LOGGING.depth = getattr(IN_LOGGING, "depth", 0) + 1
            try:
                return _orig_handler_handle(handler_self, record)
            finally:
                IN_LOGGING.depth -= 1

        setattr(Handler, "handle", _protected_handle)

        self._log_handler = EditorConsoleHandler(dispatch=lambda s, t: self.dispatch({"stream": s, "text": t}))
        Logger.root.addHandler(self._log_handler)
        self._installed = True

    def _uninstall_global_hooks(self) -> None:
        self._stdout_redirector.uninstall()
        self._stderr_redirector.uninstall()

        if self._orig_handler_handle is not None:
            setattr(Handler, "handle", self._orig_handler_handle)
            self._orig_handler_handle = None

        if self._log_handler is not None:
            Logger.root.removeHandler(self._log_handler)
            self._log_handler = None
        self._installed = False
