from typing import Any

from jetpytools import CustomRuntimeError, copy_signature
from PySide6.QtWidgets import QLayout, QToolBar


class DiagramToolBar(QToolBar):
    @copy_signature(QToolBar.__init__)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.current_layout.setContentsMargins(8, 8, 8, 4)
        self.current_layout.setSpacing(6)

    @property
    def current_layout(self) -> QLayout:
        if layout := self.layout():
            return layout

        raise CustomRuntimeError
