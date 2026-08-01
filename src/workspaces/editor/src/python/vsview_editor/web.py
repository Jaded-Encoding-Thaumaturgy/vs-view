from __future__ import annotations

import json
from collections.abc import Mapping
from logging import DEBUG, ERROR, WARNING, getLogger
from pathlib import Path
from typing import Any, override

from jetpytools import CustomNotImplementedError, copy_signature
from PySide6.QtCore import QEvent, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QPalette, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMessageBox, QWidget

from vsview.env import getenv_bool

from .logging import js_logger
from .lsp import LSPConfig
from .utils import SafeSlot

logger = getLogger(__name__)


class MonacoBridge(QObject):
    """Bridge object registered on QWebChannel for Python <-> JS communication."""

    # Python -> JS dispatch signal
    dispatchCommandSignal = Signal(str, str)
    """Dispatch generic command (command_id, payload_json) to JavaScript."""

    # Python-side signals for internal use
    editorReady = Signal()
    """Called when Monaco finishes initialization."""
    contentChanged = Signal(str)
    """Called when the editor content changes (debounced)."""
    mainContentChanged = Signal(str)
    """Called when the Main script content changes (debounced)."""
    cursorPositionChanged = Signal(int, int)
    """Called when editor cursor position changes (line, column)."""
    activeTabChanged = Signal(str)
    """Called when the active tab in Monaco changes."""
    saveRequested = Signal()
    """Called when Monaco requests saving the current script."""
    saveAsRequested = Signal()
    """Called when Monaco requests saving the current script as a new file."""
    formatRequested = Signal()
    """Called when Monaco requests formatting the current script."""
    generateStubsRequested = Signal()
    """Called when Monaco requests generating VapourSynth stubs."""
    consoleResized = Signal(int)
    """Called when console viewport width changes (cols)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._content = ""
        self._main_content = ""
        self._ready = False

    @property
    def content(self) -> str:
        return self._content

    @content.setter
    def content(self, text: str) -> None:
        self._content = text
        self.dispatch("editor.setValue", {"text": text})

    @property
    def main_content(self) -> str:
        return self._main_content or self._content

    @property
    def ready(self) -> bool:
        return self._ready

    def dispatch(self, command: str, payload_json: Any = None) -> None:
        """Dispatch a command payload to the JavaScript frontend."""
        payload_str = json.dumps(payload_json) if payload_json is not None else ""
        self.dispatchCommandSignal.emit(command, payload_str)

    def set_theme(self, theme: str) -> None:
        """Change the Monaco editor theme."""
        self.dispatch("editor.setTheme", {"theme": theme})

    def set_font_size(self, size: int) -> None:
        """Change the Monaco editor font size."""
        self.dispatch("editor.setFontSize", {"size": size})

    def set_language(self, language: str) -> None:
        """Change the Monaco editor language mode."""
        self.dispatch("editor.setLanguage", {"lang": language})

    def connect_lsp(self, port: int, config: LSPConfig) -> None:
        """Notify JS to connect to LSP WebSocket port for a specified LSP configuration."""
        payload: dict[str, Any] = {
            "id": config.id,
            "name": config.name,
            "port": port,
            "language": config.language,
            "fileEventsPattern": config.file_events_pattern,
        }
        if config.configuration_section is not None:
            payload["configurationSection"] = config.configuration_section

        self.dispatch("lsp.connect", payload)

    def disconnect_lsp(self, server_id: str | None = None) -> None:
        """Notify JS to disconnect an LSP client (or all clients if server_id is None)."""
        self.dispatch("lsp.disconnect", {"id": server_id} if server_id is not None else {})

    def toggle_word_wrap(self) -> None:
        """Toggle word wrap in Monaco."""
        self.dispatch("editor.toggleWordWrap")

    def toggle_console(self) -> None:
        """Toggle the output console panel in frontend."""
        self.dispatch("console.toggle")

    def clear_console(self) -> None:
        """Clear the output console panel buffer in frontend."""
        self.dispatch("console.clear")

    def update_lsp_settings(self, section: str, settings_json: str) -> None:
        """Push updated LSP settings JSON to Monaco for specified configuration section."""
        self.dispatch("editor.updateLspSettings", {"section": section, "settingsJson": settings_json})

    def update_editor_options(self, options_json: str) -> None:
        """Push updated Monaco editor options JSON to JavaScript."""
        self.dispatch("editor.updateOptions", {"optionsJson": options_json})

    def trigger_save(self) -> None:
        """Notify JS to flush content and trigger save request."""
        self.dispatch("editor.triggerSave")

    def trigger_save_as(self) -> None:
        """Notify JS to flush content and trigger save-as request."""
        self.dispatch("editor.triggerSaveAs")

    def trigger_format(self) -> None:
        """Notify JS to flush content and trigger format request."""
        self.dispatch("editor.triggerFormat")

    def open_tab(self, uri: str, content: str, language: str = "python", is_main: bool = False) -> None:
        """Open or focus a tab in Monaco."""
        self.dispatch("editor.openTab", {"uri": uri, "content": content, "language": language, "isMain": is_main})

    def close_tab(self, uri: str) -> None:
        """Close a tab in Monaco."""
        self.dispatch("editor.closeTab", {"uri": uri})

    def select_tab(self, uri: str) -> None:
        """Select active tab in Monaco."""
        self.dispatch("editor.selectTab", {"uri": uri})

    def set_main_tab(self, uri: str) -> None:
        """Set designated tab as Main script in Monaco."""
        self.dispatch("editor.setMainTab", {"uri": uri})

    def tab_saved(self, uri: str, old_uri: str = "") -> None:
        """Notify JS that the tab has been saved."""
        self.dispatch("editor.tabSaved", {"uri": uri, "oldUri": old_uri})

    # Slots called from JavaScript
    @Slot()
    def onEditorReady(self) -> None:
        logger.debug("Monaco editor is ready (Python)")
        self._ready = True
        self.editorReady.emit()

    @Slot(str)
    def onContentChanged(self, content: str) -> None:
        self._content = content
        self.contentChanged.emit(content)

    @Slot(str)
    def onMainContentChanged(self, main_content: str) -> None:
        self._main_content = main_content
        self.mainContentChanged.emit(main_content)

    @Slot(int, int)
    def onCursorPositionChanged(self, line: int, column: int) -> None:
        self.cursorPositionChanged.emit(line, column)

    @Slot(str)
    def onActiveTabChanged(self, uri: str) -> None:
        self.activeTabChanged.emit(uri)

    @Slot()
    def requestSave(self) -> None:
        self.saveRequested.emit()

    @Slot()
    def requestSaveAs(self) -> None:
        self.saveAsRequested.emit()

    @Slot()
    def requestFormat(self) -> None:
        self.formatRequested.emit()

    @Slot()
    def requestGenerateStubs(self) -> None:
        self.generateStubsRequested.emit()

    @Slot(int)
    def onConsoleResized(self, cols: int) -> None:
        self.consoleResized.emit(cols)

    @SafeSlot(str, result=dict)
    def statFile(self, filepath: str) -> dict[str, Any] | None:
        """Get stat metadata for a file on disk (used by Monaco FileSystemProvider)."""
        if not (p := Path(filepath)).exists():
            return None

        st = p.stat()
        return {
            "type": 2 if p.is_dir() else 1,
            "ctime": int(getattr(st, "st_birthtime", st.st_ctime) * 1000),  # pyright: ignore[reportDeprecated],
            "mtime": int(st.st_mtime * 1000),
            "size": st.st_size,
        }

    @SafeSlot(str, result=str)
    def readFile(self, filepath: str) -> str | None:
        """Read content of a file on disk (used by Monaco FileSystemProvider)."""
        return p.read_text(encoding="utf-8", errors="replace") if (p := Path(filepath)).is_file() else None


class MonacoWebPage(QWebEnginePage):
    LOG_LEVELS: Mapping[QWebEnginePage.JavaScriptConsoleMessageLevel, int] = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: DEBUG,
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: WARNING,
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: ERROR,
    }

    @property
    def view(self) -> MonacoEditorWidget:
        if isinstance(view := self.parent(), MonacoEditorWidget):
            return view
        raise CustomNotImplementedError

    @override
    def javaScriptAlert(self, security_origin: QUrl | str, msg: str) -> None:
        QMessageBox.information(self.view, "Alert", msg, QMessageBox.StandardButton.Ok)

    @override
    def javaScriptConfirm(self, security_origin: QUrl | str, msg: str) -> bool:
        result = QMessageBox.question(
            self.view,
            "Confirm",
            msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Ok

    @override
    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        if source_id.startswith("file:///"):
            source_id = Path(source_id[8:]).name

        lvl = self.LOG_LEVELS.get(level, DEBUG)
        js_logger.log(lvl, message, extra={"js_source": source_id, "js_lineno": line_number})


class MonacoEditorWidget(QWebEngineView):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setPage(MonacoWebPage(self, backgroundColor=self.palette().color(QPalette.ColorRole.Window)))

        # Bridge for Python <-> JS communication
        self.bridge = MonacoBridge(self)

        # Set up QWebChannel
        channel = QWebChannel(self, propertyUpdateInterval=-1)
        channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(channel)

        # Inject environment configuration into JavaScript before document creation
        env_data = {"VSVIEW_DEBUG": getenv_bool("VSVIEW_DEBUG")}
        script = QWebEngineScript()
        script.setName("vsview_env_injection")
        script.setSourceCode(f"window.ENV = Object.freeze({json.dumps(env_data)});\n")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self.page().scripts().insert(script)

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.page().setBackgroundColor(self.palette().color(QPalette.ColorRole.Window))

    @copy_signature(QWebEngineView.load)
    @override
    def load(self, thing: Any, /) -> None:
        logger.debug("Loading Monaco editor from %s", thing)
        return super().load(thing)
