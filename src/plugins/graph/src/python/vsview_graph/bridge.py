from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal, NotRequired, Self, TypedDict, overload, override

from vsview.api import BaseWebChannelBridge


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


class OutputItem(TypedDict):
    index: int
    name: str
    isActive: bool


class SetOutputsPayload(TypedDict):
    outputs: Sequence[OutputItem]


class GraphOptionsPayload(TypedDict):
    direction: Literal["TB", "LR"]
    layout: Literal["dagre", "breadthfirst", "concentric", "grid"]
    rankSep: int
    nodeSep: int
    heatmap: bool
    showTimings: bool
    timingUnit: Literal["auto", "ms", "ns"]
    autoFitOnReload: bool


class NodeTimingUpdate(TypedDict):
    id: str
    processing_time_ns: int
    processing_time_ms: float


class UpdateTimingsPayload(TypedDict):
    timings: Sequence[NodeTimingUpdate]


class SelectNodePayload(TypedDict):
    id: str


class KeybindingRule(TypedDict):
    action: str
    key: str
    ctrl: bool
    shift: bool
    alt: bool
    meta: bool


class SetKeybindingsPayload(TypedDict):
    keybindings: Sequence[KeybindingRule]


class RequestPngExportPayload(TypedDict):
    destination: Literal["clipboard", "file"]
    path: NotRequired[str]


class ThemeTokens(TypedDict):
    isDark: bool
    bg: str
    surface: str
    surfaceElevated: str
    surfaceHover: str
    border: str
    textPrimary: str
    textSecondary: str
    textMuted: str
    accent: str
    accentAmber: str
    accentEmerald: str
    danger: str


class GraphBridge(BaseWebChannelBridge[PyToJsCommand, JsToPyCommand]):
    @overload  # type: ignore[override]
    def dispatch(self, command: Literal[PyToJsCommand.FIT_VIEW, PyToJsCommand.RESET_VIEW]) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SET_DATA], payload: Mapping[str, Any]) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SET_OPTIONS], payload: GraphOptionsPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.UPDATE_TIMINGS], payload: UpdateTimingsPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SET_OUTPUTS], payload: SetOutputsPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SELECT_NODE], payload: SelectNodePayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SET_KEYBINDINGS], payload: SetKeybindingsPayload) -> None: ...
    @overload
    def dispatch(
        self, command: Literal[PyToJsCommand.REQUEST_PNG_EXPORT], payload: RequestPngExportPayload
    ) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.THEME_UPDATE], payload: ThemeTokens) -> None: ...
    @override
    def dispatch(self, command: PyToJsCommand, payload: Mapping[str, Any] | None = None) -> None:
        super().dispatch(command, payload)
