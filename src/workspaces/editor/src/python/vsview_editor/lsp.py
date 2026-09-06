from __future__ import annotations

import itertools
import re
import shutil
import sys
import urllib.parse
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from logging import DEBUG, getLogger
from pathlib import Path

from jetpytools import CustomValueError
from PySide6.QtCore import QObject, QProcess, QUrl, Slot
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from vsview.api import QObjectSet

logger = getLogger(__name__)


@dataclass(frozen=True)
class LSPConfig:
    id: str
    name: str
    command: Sequence[str]
    language: str
    file_events_pattern: str | None
    configuration_section: str | Sequence[str] | None = None
    progress_notifications: Sequence[Mapping[str, str]] | None = None

    def __post_init__(self) -> None:
        if not self.command:
            raise CustomValueError("LSP config %r has an empty command list.", self.id)


LSP_BASEDPYRIGHT_CONFIG = LSPConfig(
    id="basedpyright",
    name="Basedpyright Language Server",
    command=["basedpyright-langserver", "--stdio"],
    language="python",
    file_events_pattern="**/*.py",
    configuration_section=["basedpyright", "python"],
    progress_notifications=[{"begin": "pyright/beginAnalysis", "end": "pyright/endAnalysis"}],
)


class LSPProcessServer(QObject):
    """Manages a single LSP subprocess and its associated WebSocket bridge server."""

    def __init__(self, config: LSPConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.server: QWebSocketServer | None = None
        self.clients = QObjectSet[QWebSocket]()
        self.process: QProcess | None = None
        self._stdout_buffer = bytearray()
        self.real_uri: str | None = None
        self.uri_variations = list[str]()
        self._virtual_tab_pattern: re.Pattern[str] | None = None

    def start(self, port: int = 0, workspace_dir: Path | None = None) -> int:
        if not workspace_dir:
            workspace_dir = Path.cwd()

        self.real_uri = workspace_dir.as_uri()

        # Build URI variations to replace in JSON-RPC messages from server -> client
        self.uri_variations = self._build_uri_variations(workspace_dir)
        escaped_vars = "|".join(re.escape(v.rstrip("/")) for v in self.uri_variations)
        self._virtual_tab_pattern = re.compile(
            rf"(?:{escaped_vars})/(script\.py|untitled_[a-zA-Z0-9_-]+\.py|workspace\.code-workspace)"
        )
        logger.log(DEBUG - 1, "URI Variations: %s", self.uri_variations)

        executable_name = Path(self.config.command[0])
        if executable_name.is_file():
            binary = executable_name.resolve()
        elif not (binary := shutil.which(executable_name)):
            logger.error("LSP executable %r for %r not found in PATH.", executable_name, self.config.id)
            return -1

        self.server = QWebSocketServer(f"LSP Bridge ({self.config.name})", QWebSocketServer.SslMode.NonSecureMode, self)
        if not self.server.listen(QHostAddress.SpecialAddress.LocalHost, port):
            logger.error("Failed to start LSP WebSocket server for %r: %s", self.config.id, self.server.errorString())
            return -1

        actual_port = self.server.serverPort()
        logger.debug("LSP WebSocket server for %r listening on 127.0.0.1:%d", self.config.id, actual_port)

        self.server.newConnection.connect(self._on_new_connection)

        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._on_stdout_ready)
        self.process.readyReadStandardError.connect(self._on_stderr_ready)
        self.process.errorOccurred.connect(self._on_process_error)

        try:
            self.process.start(str(binary), self.config.command[1:])
        except Exception:
            logger.exception("Failed to launch LSP subprocess for %r:", self.config.id)

        return actual_port

    def stop(self) -> None:
        if self.clients:
            for client in self.clients:
                if client.isValid():
                    client.close()
                client.deleteLater()
            self.clients.clear()

        if self.server is not None:
            server = self.server
            self.server = None
            server.close()
            server.deleteLater()

        if self.process is not None:
            proc = self.process
            self.process = None
            logger.debug("Stopping LSP QProcess for %r (current state: %s)", self.config.id, proc.state())
            proc.readyReadStandardOutput.disconnect(self._on_stdout_ready)
            proc.readyReadStandardError.disconnect(self._on_stderr_ready)
            proc.errorOccurred.disconnect(self._on_process_error)

            if proc.state() != QProcess.ProcessState.NotRunning:
                if sys.platform == "win32":
                    proc.kill()
                    if not proc.waitForFinished(3000):
                        logger.warning(
                            "LSP QProcess kill timed out for %r, state is still %s",
                            self.config.id,
                            proc.state(),
                        )
                else:
                    proc.terminate()
                    if not proc.waitForFinished(1000):
                        proc.kill()
                        proc.waitForFinished(1000)

            proc.deleteLater()

    @Slot()
    def _on_new_connection(self) -> None:
        if not self.server:
            return
        client = self.server.nextPendingConnection()
        self.clients.add(client)
        logger.debug("New LSP WebSocket connection established for %r", self.config.id)

        client.textMessageReceived.connect(self._on_ws_message)

        client_weak = weakref.ref(client)

        @Slot()
        def _on_client_disconnected() -> None:
            if c := client_weak():
                self.clients.discard(c)
            logger.debug("LSP WebSocket connection closed for %r (Python)", self.config.id)

        client.disconnected.connect(_on_client_disconnected)

    @Slot(str)
    def _on_ws_message(self, message: str) -> None:
        """
        Receive JSON-RPC from frontend WebSocket, translate virtual URI to real URI and forward to LSP stdin.
        """
        if not self.process or self.process.state() != QProcess.ProcessState.Running:
            logger.warning("Cannot write to LSP stdin for %r: process is not running.", self.config.id)
            return

        if self.real_uri and "file:///workspace" in message:
            message = message.replace("file:///workspace", self.real_uri)

        body = message.encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self.process.write(header + body)

    @Slot()
    def _on_stdout_ready(self) -> None:
        if not self.process:
            return

        self._stdout_buffer.extend(self.process.readAllStandardOutput().data())

        while True:
            header_end = self._stdout_buffer.find(b"\r\n\r\n")
            if header_end == -1:
                break

            header_bytes = self._stdout_buffer[:header_end]
            content_length = None
            for line in header_bytes.split(b"\r\n"):
                if line.startswith(b"Content-Length:"):
                    content_length = int(line.split(b":")[1].strip())
                    break

            if content_length is None:
                del self._stdout_buffer[: header_end + 4]
                continue

            total_needed = header_end + 4 + content_length
            if len(self._stdout_buffer) < total_needed:
                break  # Incomplete message body; await next read event

            body_bytes = self._stdout_buffer[header_end + 4 : total_needed]
            del self._stdout_buffer[:total_needed]

            msg_str = body_bytes.decode("utf-8", errors="replace")
            if self._virtual_tab_pattern is not None:
                msg_str = self._virtual_tab_pattern.sub(r"file:///workspace/\1", msg_str)

            for client in self.clients:
                if client.isValid():
                    client.sendTextMessage(msg_str)

    @Slot()
    def _on_stderr_ready(self) -> None:
        if not self.process:
            return
        output = bytes(self.process.readAllStandardError().data()).decode("utf-8", errors="replace").strip()
        if output:
            for line in output.splitlines():
                logger.debug("[LSP %s stderr] %s", self.config.id, line)

    @Slot(QProcess.ProcessError)
    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if self.process:
            logger.error("LSP QProcess error for %r (%s): %s", self.config.id, error, self.process.errorString())

    @staticmethod
    def _build_uri_variations(workspace_dir: Path | str) -> list[str]:
        if isinstance(workspace_dir, str) and workspace_dir.startswith("file:"):
            raw_uri = workspace_dir
        elif isinstance(workspace_dir, Path) and workspace_dir.is_absolute():
            raw_uri = workspace_dir.as_uri()
        else:
            raw_uri = QUrl.fromLocalFile(str(workspace_dir)).toString()

        variations = set[str]()

        # Get path part after scheme
        if raw_uri.startswith("file:///"):
            path_part = raw_uri[8:]
        elif raw_uri.startswith("file://"):
            path_part = raw_uri[7:]
        elif raw_uri.startswith("file:/"):
            path_part = raw_uri[6:]
        else:
            path_part = raw_uri

        # Identify drive letter if present
        if len(path_part) >= 2 and path_part[1] == ":":
            drive = path_part[0]
            rest = path_part[2:]
            drives = [drive.lower(), drive.upper()]
            colons = [":", "%3A", "%3a"]
            has_drive = True
        elif len(path_part) >= 4 and path_part[1:4].upper() == "%3A":
            drive = path_part[0]
            rest = path_part[4:]
            drives = [drive.lower(), drive.upper()]
            colons = [":", "%3A", "%3a"]
            has_drive = True
        else:
            drives = [""]
            colons = [""]
            rest = "/" + path_part.lstrip("/")
            has_drive = False

        rest_unquoted = urllib.parse.unquote(rest)
        rest_quoted = urllib.parse.quote(rest_unquoted, safe="/")
        rest_options = {rest, rest_unquoted, rest_quoted}

        if has_drive:
            for prefix, d, c, r in itertools.product(["file:///", "file://", "file:/"], drives, colons, rest_options):
                base = f"{prefix}{d}{c}{r}"
                variations.add(base)
                variations.add(urllib.parse.quote(base, safe=":/%"))
                variations.add(urllib.parse.unquote(base))
        else:
            for prefix, r in itertools.product(["file://", "file:"], rest_options):
                base = f"{prefix}{r}"
                variations.add(base)
                variations.add(urllib.parse.quote(base, safe=":/%"))
                variations.add(urllib.parse.unquote(base))

        return sorted(variations, key=len, reverse=True)


class LSPProcessManager(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.servers = dict[str, LSPProcessServer]()

    def start_server(
        self,
        config: LSPConfig,
        port: int = 0,
        workspace_dir: Path | None = None,
    ) -> int:
        if config.id in self.servers:
            logger.debug("LSP server %r is already running. Stopping existing server first.", config.id)
            self.stop_server(config.id)

        server = LSPProcessServer(config, self)
        actual_port = server.start(port=port, workspace_dir=workspace_dir)
        if actual_port > 0:
            self.servers[config.id] = server
            return actual_port
        return -1

    def stop_server(self, server_id: str) -> None:
        if server := self.servers.pop(server_id, None):
            server.stop()

    def stop(self, server_id: str | None = None) -> None:
        if server_id is not None:
            self.stop_server(server_id)
        else:
            for server in list(self.servers.values()):
                server.stop()
            self.servers.clear()
