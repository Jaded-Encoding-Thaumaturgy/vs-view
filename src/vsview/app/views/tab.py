"""Custom tab widget implementations for video output views."""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from jetpytools import copy_signature
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QTabWidget, QWidget

from .video import GraphicsView


class TabViewWidget(QTabWidget):
    """
    A custom QTabWidget which only contains GraphicsViews.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.recent_views = OrderedDict[GraphicsView, None]()
        self.currentChanged.connect(self._on_current_changed)

    @property
    def previous_view(self) -> GraphicsView:
        """Get the previously active view, or the only view if just one exists."""
        views = list(self.recent_views)
        return views[-2] if len(views) >= 2 else views[0]

    @copy_signature(QTabWidget.addTab)
    def addTab(self, *args: Any) -> int:
        if not isinstance(args[0], GraphicsView):
            raise ValueError("TabViewWidget can only contain GraphicsViews")

        self.recent_views[args[0]] = None

        return super().addTab(*args)

    def removeTab(self, index: int, /) -> None:
        widget = self.widget(index)

        if isinstance(widget, GraphicsView) and widget in self.recent_views:
            del self.recent_views[widget]

        super().removeTab(index)

    def currentWidget(self) -> GraphicsView:
        """
        Return the currently selected GraphicsView.

        Raises:
            ValueError: If the current widget is not a GraphicsView.
        """
        view = super().currentWidget()

        if not isinstance(view, GraphicsView):
            raise ValueError("Current widget is not a GraphicsView")

        return view

    def setCurrentWidget(self, widget: QWidget) -> None:
        """
        Set the current widget to the specified GraphicsView.

        Raises:
            ValueError: If the widget is not a GraphicsView.
        """
        if not isinstance(widget, GraphicsView):
            raise ValueError("TabViewWidget can only contain GraphicsViews")

        return super().setCurrentWidget(widget)

    def deleteLater(self) -> None:
        self.clear()
        return super().deleteLater()

    def clear(self) -> None:
        self.recent_views.clear()

        for view in self.views():
            view.clear_scene()
            view.deleteLater()

        return super().clear()

    def view(self, index: int) -> GraphicsView:
        """
        Return the GraphicsView at the specified index.

        Raises:
            ValueError: If the widget is not a GraphicsView.
        """
        widget = super().widget(index)

        if not isinstance(widget, GraphicsView):
            raise ValueError("Widget is not a GraphicsView")

        return widget

    def views(self) -> Iterator[GraphicsView]:
        for i in range(self.count()):
            widget = self.widget(i)
            if isinstance(widget, GraphicsView):
                yield widget

    def reset_views(self) -> None:
        for view in self.views():
            view.reset_scene()

    def get_tab_label(self, index: int) -> TabLabel:
        if isinstance(label := self.tabBar().tabButton(index, self.tabBar().ButtonPosition.LeftSide), TabLabel):
            return label

        raise ValueError("Tab label not found")

    @contextmanager
    def disabled(self) -> Iterator[None]:
        self.setDisabled(True)
        yield
        self.setDisabled(False)

    def _on_current_changed(self, index: int) -> None:
        if index == -1:
            return

        widget = self.widget(index)

        if isinstance(widget, GraphicsView):
            if widget in self.recent_views:
                self.recent_views.move_to_end(widget)
            else:
                self.recent_views[widget] = None


class TabLabel(QWidget):
    """Custom widget for tab labels displaying output name and zoom."""

    def __init__(self, vs_name: str | None, vs_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._name = vs_name or "Node"
        self._vs_index = vs_index
        self._zoom = 1.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Name label (can be elided)
        self._name_label = QLabel(self)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._name_label.setMinimumWidth(40)
        self._name_label.setMaximumWidth(150)
        layout.addWidget(self._name_label)

        self._zoom_label = QLabel(self)
        self._zoom_label.setMinimumWidth(50)
        layout.addWidget(self._zoom_label)

        self._update_text()

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        self._update_text()

    @property
    def vs_index(self) -> int:
        return self._vs_index

    @vs_index.setter
    def vs_index(self, value: int) -> None:
        self._vs_index = value
        self._update_text()

    @property
    def zoom(self) -> float:
        """The current zoom factor, or 0 if not yet calculated."""
        return self._zoom

    @zoom.setter
    def zoom(self, zoom: float) -> None:
        """
        Update the zoom percentage display.

        Args:
            zoom: The zoom factor (1.0 = 100%), or 0 to show placeholder.
        """
        self._zoom = zoom
        self._update_text()

    def _update_text(self) -> None:
        name_text = f"{self.vs_index}: {self.name}"
        metrics = QFontMetrics(self._name_label.font())

        self._name_label.setText(
            metrics.elidedText(name_text, Qt.TextElideMode.ElideRight, self._name_label.maximumWidth())
        )
        self._zoom_label.setText(f"({round(self._zoom * 100)}%)" if self._zoom else "(---%)")
