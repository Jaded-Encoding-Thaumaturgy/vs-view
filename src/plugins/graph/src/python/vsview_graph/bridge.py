from __future__ import annotations

import json
from enum import StrEnum
from logging import getLogger
from typing import Any, Self

from PySide6.QtCore import QObject, Signal, Slot

logger = getLogger(__name__)


class PyToJsCommand(StrEnum):
    SET_DATA = "graph.setData"
    SET_OPTIONS = "graph.setOptions"
    UPDATE_TIMINGS = "graph.updateTimings"
    SET_OUTPUTS = "graph.setOutputs"
    SELECT_NODE = "graph.selectNode"
    SET_KEYBINDINGS = "graph.setKeybindings"
    FIT_VIEW = "graph.fitView"
    RESET_VIEW = "graph.resetView"
    REQUEST_PNG_EXPORT = "graph.requestPngExport"
    THEME_UPDATE = "theme.update"


class JsToPyCommand(StrEnum):
    READY = "graph.ready"
    RELOAD = "graph.reload"
    EXPORT_GRAPH = "graph.exportGraph"
    SAVE_IMAGE = "graph.saveImage"
    OUTPUT_SELECT = "output.select"
    NODE_SELECT = "node.select"
    NODE_DESELECT = "node.deselect"
    NODE_COPY = "node.copy"
    CONTEXT_MENU = "contextmenu.request"


class NodeCopyTarget(StrEnum):
    """Supported node copy targets from the inspector drawer."""

    description: str

    ID = "id", "node id"
    CALL = "call", "filter call"
    REPORT = "report", "node report"

    def __new__(cls, value: str, description: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.description = description
        return obj


class ExportFormat(StrEnum):
    suffix: str
    file_filter: str
    label: str

    DOT = "dot", ".dot", "Graphviz DOT (*.dot)", "DOT"
    MERMAID = "mermaid", ".mmd", "Mermaid flowchart (*.mmd)", "Mermaid"
    ASCII = "ascii", ".txt", "Plain text (*.txt)", "ASCII"
    JSON = "json", ".json", "JSON (*.json)", "JSON"

    def __new__(cls, value: str, suffix: str = "", file_filter: str = "", label: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.suffix = suffix
        obj.file_filter = file_filter
        obj.label = label
        return obj


class GraphBridge(QObject):
    # Python -> JavaScript command dispatch signal (channel transport)
    dispatchCommandSignal = Signal(str, str)
    # Lifecycle signal emitted when web frontend completes initialization
    webReady = Signal()
    # Unified signal emitted when any command arrives from JavaScript
    commandReceived = Signal(JsToPyCommand, dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._is_ready = False

    @property
    def is_ready(self) -> bool:
        """Whether the frontend web view has completed initialization."""
        return self._is_ready

    def dispatch(self, command: PyToJsCommand, payload: Any = None) -> None:
        payload_str = json.dumps(payload) if payload is not None else ""
        self.dispatchCommandSignal.emit(command, payload_str)

    @Slot()
    def onWebReady(self) -> None:
        """Invoked by JavaScript when the page and QWebChannel are initialized."""
        logger.debug("Graph web frontend is ready.")
        self._is_ready = True
        self.webReady.emit()

    @Slot(str, str)
    def dispatchFromJs(self, command: str, payload_json: str) -> None:
        """Receive an action or event from the JavaScript frontend and forward via commandReceived."""
        try:
            command = JsToPyCommand(command)
            parsed = json.loads(payload_json)
            payload = parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.error("Failed to decode JSON from JS dispatch '%s': %s", command, e)
            return

        self.commandReceived.emit(command, payload)
