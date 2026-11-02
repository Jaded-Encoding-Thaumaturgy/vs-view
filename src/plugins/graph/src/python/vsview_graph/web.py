from __future__ import annotations

from collections.abc import Mapping
from logging import DEBUG, ERROR, WARNING, getLogger
from typing import Any, override

from jetpytools import copy_signature
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QPalette, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from vsview.api import DefaultWebChannelScript, VSViewEnInjectionScript, setup_web_logger

from .bridge import GraphBridge, PyToJsCommand
from .theme import extract_theme_tokens

logger = getLogger(__name__)
js_logger = setup_web_logger("vsview_graph_js")


class GraphWebPage(QWebEnginePage):
    LOG_LEVELS: Mapping[QWebEnginePage.JavaScriptConsoleMessageLevel, int] = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: DEBUG,
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: WARNING,
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: ERROR,
    }

    def __init__(self, parent: QObject, background_color: QColor | None = None) -> None:
        super().__init__(parent)
        if background_color is not None:
            self.setBackgroundColor(background_color)

    @override
    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        js_logger.log(
            self.LOG_LEVELS.get(level, DEBUG),
            message,
            extra={"js_source": source_id, "js_lineno": line_number},
        )


class GraphWebView(QWebEngineView):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setPage(GraphWebPage(self, background_color=self.palette().color(QPalette.ColorRole.Window)))

        # Python <-> JS Bridge
        self.bridge = GraphBridge(self)

        # QWebChannel
        channel = QWebChannel(self, propertyUpdateInterval=-1)
        channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(channel)

        self.page().scripts().insert(DefaultWebChannelScript())
        self.page().scripts().insert(VSViewEnInjectionScript())

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.page().setBackgroundColor(self.palette().color(QPalette.ColorRole.Window))
            self.sync_theme()

    @copy_signature(QWebEngineView.load)
    @override
    def load(self, thing: Any, /) -> None:
        logger.debug("Loading Graph from %s", thing)
        return super().load(thing)

    @override
    def deleteLater(self) -> None:
        self.page().webChannel().deregisterObject(self.bridge)
        self.page().webChannel().deleteLater()
        self.page().deleteLater()
        super().deleteLater()

    def sync_theme(self) -> bool:
        if not self.bridge.is_ready:
            return False
        tokens = extract_theme_tokens(self.palette())
        self.bridge.dispatch(PyToJsCommand.THEME_UPDATE, tokens)
        return True
