from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, NotRequired, TypedDict, overload, override

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QWidget

from vsview.api import BaseWebChannelBridge, BaseWebEngineView, SafeSlot

from .utils import extract_supported_script_files


class PyToJsCommand(StrEnum):
    APPLY_FORMAT = "editor.applyFormat"
    SET_THEME = "editor.setTheme"
    TOGGLE_WORD_WRAP = "editor.toggleWordWrap"
    UPDATE_LSP_SETTINGS = "editor.updateLspSettings"
    UPDATE_OPTIONS = "editor.updateOptions"
    OPEN_TAB = "editor.openTab"
    SELECT_TAB = "editor.selectTab"
    TAB_SAVED = "editor.tabSaved"
    TRIGGER_SAVE = "editor.triggerSave"
    TRIGGER_SAVE_AS = "editor.triggerSaveAs"
    TRIGGER_FORMAT = "editor.triggerFormat"
    TRIGGER_RUN = "editor.triggerRun"
    CONSOLE_APPEND = "console.append"
    CONSOLE_TOGGLE = "console.toggle"
    LSP_CONNECT = "lsp.connect"
    LSP_DISCONNECT = "lsp.disconnect"


class JsToPyCommand(StrEnum):
    MAIN_CONTENT_CHANGED = "editor.mainContentChanged"
    CURSOR_POSITION_CHANGED = "editor.cursorPositionChanged"
    ACTIVE_TAB_CHANGED = "editor.activeTabChanged"
    MAIN_TAB_CHANGED = "editor.mainTabChanged"
    TAB_STATE_CHANGED = "editor.tabStateChanged"
    SAVE_REQUESTED = "editor.saveRequested"
    SAVE_AS_REQUESTED = "editor.saveAsRequested"
    FORMAT_REQUESTED = "editor.formatRequested"
    RUN_REQUESTED = "editor.runRequested"
    GENERATE_STUBS_REQUESTED = "editor.generateStubsRequested"
    RESTART_LSP_REQUESTED = "lsp.restartRequested"
    CONSOLE_RESIZED = "console.resized"
    COPY_TO_CLIPBOARD = "clipboard.copy"


class ApplyFormatPayload(TypedDict):
    uri: str
    versionId: int
    text: str


class SetThemePayload(TypedDict):
    theme: str


class UpdateLspSettingsPayload(TypedDict):
    section: str
    settingsJson: str


class UpdateOptionsPayload(TypedDict):
    optionsJson: str


class OpenTabPayload(TypedDict):
    uri: str
    content: str
    language: NotRequired[str]
    isMain: NotRequired[bool]
    replaceUri: NotRequired[str]


class SelectTabPayload(TypedDict):
    uri: str


class TabSavedPayload(TypedDict):
    uri: str
    oldUri: str
    versionId: NotRequired[int]


class ConsoleAppendPayload(TypedDict):
    stream: NotRequired[str]
    text: str


class ConsoleTogglePayload(TypedDict):
    state: NotRequired[bool]


class LspConnectPayload(TypedDict):
    id: str
    name: str
    port: int
    path: str
    language: str
    configurationSection: NotRequired[str | Sequence[str]]
    progressNotifications: NotRequired[Sequence[Mapping[str, str]]]


class LspDisconnectPayload(TypedDict):
    id: NotRequired[str]


class TabInfo(TypedDict):
    uri: str
    title: str
    isMain: bool
    isDirty: bool
    language: str
    # Sent only when the tab's version changed since the last message, always with versionId.
    content: NotRequired[str]
    versionId: NotRequired[int]


class MonacoBridge(BaseWebChannelBridge[PyToJsCommand, JsToPyCommand]):
    """Bridge object registered on QWebChannel for Python <-> JS communication."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._main_content = ""

    @property
    def main_content(self) -> str:
        return self._main_content

    @overload  # type: ignore[override]
    def dispatch(
        self,
        command: Literal[
            PyToJsCommand.TOGGLE_WORD_WRAP,
            PyToJsCommand.TRIGGER_SAVE,
            PyToJsCommand.TRIGGER_SAVE_AS,
            PyToJsCommand.TRIGGER_FORMAT,
            PyToJsCommand.TRIGGER_RUN,
        ],
    ) -> None: ...
    @overload
    def dispatch(
        self,
        command: Literal[PyToJsCommand.CONSOLE_TOGGLE],
        payload: ConsoleTogglePayload | None = None,
    ) -> None: ...
    @overload
    def dispatch(
        self,
        command: Literal[PyToJsCommand.LSP_DISCONNECT],
        payload: LspDisconnectPayload | None = None,
    ) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.APPLY_FORMAT], payload: ApplyFormatPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SET_THEME], payload: SetThemePayload) -> None: ...
    @overload
    def dispatch(
        self, command: Literal[PyToJsCommand.UPDATE_LSP_SETTINGS], payload: UpdateLspSettingsPayload
    ) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.UPDATE_OPTIONS], payload: UpdateOptionsPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.OPEN_TAB], payload: OpenTabPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.SELECT_TAB], payload: SelectTabPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.TAB_SAVED], payload: TabSavedPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.CONSOLE_APPEND], payload: ConsoleAppendPayload) -> None: ...
    @overload
    def dispatch(self, command: Literal[PyToJsCommand.LSP_CONNECT], payload: LspConnectPayload) -> None: ...
    @override
    def dispatch(self, command: PyToJsCommand, payload: Mapping[str, Any] | None = None) -> None:
        super().dispatch(command, payload)

    @override
    def on_command_received(self, command: JsToPyCommand, payload: dict[str, Any]) -> None:
        match command:
            case JsToPyCommand.MAIN_CONTENT_CHANGED:
                self._main_content = str(payload.get("content", ""))
            case JsToPyCommand.COPY_TO_CLIPBOARD:
                QApplication.clipboard().setText(str(payload.get("text", "")))

    @SafeSlot(str, result=dict, fallback={"ok": False})
    def statFile(self, filepath: str) -> dict[str, Any]:
        """Get stat metadata for a file on disk (used by Monaco FileSystemProvider)."""
        p = Path(QUrl(filepath).toLocalFile()) if filepath.startswith("file:") else Path(filepath)
        if not p.exists():
            return {"ok": False}

        st = p.stat()
        return {
            "ok": True,
            "type": 2 if p.is_dir() else 1,
            "ctime": int(getattr(st, "st_birthtime", st.st_ctime) * 1000),  # pyright: ignore[reportDeprecated],
            "mtime": int(st.st_mtime * 1000),
            "size": st.st_size,
        }

    @SafeSlot(str, result=dict, fallback={"ok": False})
    def readFile(self, filepath: str) -> dict[str, Any]:
        """Read content of a file on disk (used by Monaco FileSystemProvider)."""
        p = Path(QUrl(filepath).toLocalFile()) if filepath.startswith("file:") else Path(filepath)
        if not p.is_file():
            return {"ok": False}

        with p.open(encoding="utf-8", errors="replace", newline="") as f:
            return {"ok": True, "content": f.read()}


class MonacoEditorWidget(BaseWebEngineView[MonacoBridge]):
    logger_name: ClassVar[str] = "vsview_editor_js"

    filesDropped = Signal(list)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if extract_supported_script_files(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if extract_supported_script_files(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        if files := extract_supported_script_files(event.mimeData()):
            event.acceptProposedAction()
            self.filesDropped.emit(files)
            return
        event.ignore()
