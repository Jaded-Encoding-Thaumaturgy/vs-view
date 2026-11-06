from __future__ import annotations

from typing import ClassVar, override

from PySide6.QtCore import QEvent

from vsview.api import BaseWebEngineView

from .bridge import GraphBridge, PyToJsCommand
from .theme import extract_theme_tokens


class GraphWebView(BaseWebEngineView[GraphBridge]):
    logger_name: ClassVar[str] = "vsview_graph_js"

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.sync_theme()

    def sync_theme(self) -> bool:
        if not self.bridge.is_ready:
            return False
        tokens = extract_theme_tokens(self.palette())
        self.bridge.dispatch(PyToJsCommand.THEME_UPDATE, tokens)
        return True
