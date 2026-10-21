"""Custom tab widget implementations for video output views."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from logging import getLogger
from typing import Any, override

from jetpytools import copy_signature
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFontMetrics, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QTabWidget, QWidget

from ..themes import AppearanceManager
from .video import GraphicsView

logger = getLogger(__name__)


class BaseTabLabel(QWidget):
    """Abstract base widget for custom tab labels managing layout margins and selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = False

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)

        AppearanceManager.signals.connect_appearance_weak(self._force_selected)

    @override
    def layout(self) -> QHBoxLayout:
        return self._layout

    @property
    def is_selected(self) -> bool:
        """Whether the tab is currently selected."""
        return self._selected

    def set_selected(self, selected: bool, force: bool = False) -> None:
        """
        Update the selection state of the tab label.

        Applies the semantic palette role and updates the dynamic 'selected' property for Qt Style Sheets.
        """
        if not force and self._selected == selected and self.property("selected") is not None:
            return

        self._selected = selected
        self.setProperty("selected", selected)

        self._on_selected_changed(selected)

        # Re-polish so Qt Style Sheets evaluate BaseTabLabel[selected="true|false"]
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        for child in self.findChildren(QWidget):
            style.unpolish(child)
            style.polish(child)
            child.ensurePolished()

    def _force_selected(self) -> None:
        self.set_selected(self._selected, force=True)

    def _on_selected_changed(self, selected: bool) -> None:
        """Hook for subclasses to update child widget foreground roles."""


class SimpleTabLabel(BaseTabLabel):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._title_label = QLabel(title, self)
        self._title_label.setObjectName("tabTitleLabel")
        self._layout.addWidget(self._title_label)

        self.set_selected(False)

    @property
    def title(self) -> str:
        return self._title_label.text()

    @title.setter
    def title(self, value: str) -> None:
        self._title_label.setText(value)

    @override
    def _on_selected_changed(self, selected: bool) -> None:
        self._title_label.setForegroundRole(
            QPalette.ColorRole.Text
            if selected
            else (QPalette.ColorRole.ToolTipText if AppearanceManager.is_dark else QPalette.ColorRole.PlaceholderText)
        )


class TabLabel(BaseTabLabel):
    def __init__(self, vs_name: str, vs_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._name = vs_name
        self._vs_index = vs_index
        self._zoom = 1.0

        # Name label (can be elided)
        self._name_label = QLabel(self)
        self._name_label.setObjectName("tabNameLabel")
        self._name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._name_label.setMinimumWidth(40)
        self._name_label.setMaximumWidth(150)
        self._layout.addWidget(self._name_label)

        self._zoom_label = QLabel(self)
        self._zoom_label.setObjectName("tabZoomLabel")
        self._zoom_label.setMinimumWidth(50)
        self._layout.addWidget(self._zoom_label)

        self._update_text()
        self.set_selected(False)

    @override
    def _on_selected_changed(self, selected: bool) -> None:
        is_dark = AppearanceManager.is_dark
        role = (
            QPalette.ColorRole.Text
            if selected
            else (QPalette.ColorRole.ToolTipText if is_dark else QPalette.ColorRole.PlaceholderText)
        )
        self._name_label.setForegroundRole(role)
        self._zoom_label.setForegroundRole(role)

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
        if self._zoom == zoom:
            return

        self._zoom = zoom
        self._update_zoom_text()

    def _update_text(self) -> None:
        self._update_name_text()
        self._update_zoom_text()

    def _update_name_text(self) -> None:
        name_text = f"{self.vs_index}: {self.name}"
        metrics = QFontMetrics(self._name_label.font())

        self._name_label.setText(
            metrics.elidedText(name_text, Qt.TextElideMode.ElideRight, self._name_label.maximumWidth())
        )

        self.setToolTip(name_text)

    def _update_zoom_text(self) -> None:
        self._zoom_label.setText(f"({round(self._zoom * 100)}%)" if self._zoom else "(---%)")


class BaseTabWidget(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDocumentMode(True)
        self.tabBar().setStyleSheet("QTabBar::tab:selected { background: palette(button); }")
        self.currentChanged.connect(self._sync_tab_labels)

        AppearanceManager.signals.connect_appearance_weak(self._on_appearance_changed)

    def set_tab_label(self, index: int, label: BaseTabLabel) -> None:
        """
        Attach a BaseTabLabel to the specified tab and initialize its selection state.

        Args:
            index: The index of the tab.
            label: The custom tab label widget.
        """
        self.tabBar().setTabButton(index, self.tabBar().ButtonPosition.LeftSide, label)
        label.set_selected(index == self.currentIndex())

    def get_tab_label(self, index: int) -> BaseTabLabel | None:
        """
        Return the BaseTabLabel at the specified index, or None if not found.

        Args:
            index: The index of the tab.
        """
        btn = self.tabBar().tabButton(index, self.tabBar().ButtonPosition.LeftSide)
        return btn if isinstance(btn, BaseTabLabel) else None

    @Slot(int)
    def _sync_tab_labels(self, index: int) -> None:
        bar = self.tabBar()
        for i in range(self.count()):
            btn = bar.tabButton(i, bar.ButtonPosition.LeftSide)
            if isinstance(btn, BaseTabLabel):
                btn.set_selected(i == index)

    def _on_appearance_changed(self) -> None:
        self.tabBar().setStyleSheet(self.tabBar().styleSheet())


class TabViewWidget(BaseTabWidget):
    """
    A custom BaseTabWidget which only contains GraphicsViews.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.recent_tabs = OrderedDict[int, None]()
        self.currentChanged.connect(self._on_current_changed)

    @property
    def previous_tab_index(self) -> int:
        """Get the index of the previously active tab, or the only tab if just one exists."""
        tabs = list(self.recent_tabs)
        return tabs[-2] if len(tabs) >= 2 else tabs[0] if tabs else max(0, self.currentIndex())

    @copy_signature(QTabWidget.addTab)
    @override
    def addTab(self, *args: Any) -> int:
        if not isinstance(args[0], GraphicsView):
            raise TypeError("TabViewWidget can only contain GraphicsViews")

        return super().addTab(*args)

    @override
    def removeTab(self, index: int, /) -> None:
        if index in self.recent_tabs:
            del self.recent_tabs[index]

        super().removeTab(index)

    @override
    def currentWidget(self) -> GraphicsView:
        """
        Return the currently selected GraphicsView.

        Raises:
            TypeError: If the current widget is not a GraphicsView.
        """
        view = super().currentWidget()

        if not isinstance(view, GraphicsView):
            raise TypeError("Current widget is not a GraphicsView")

        return view

    @override
    def setCurrentWidget(self, widget: QWidget) -> None:
        """
        Set the current widget to the specified GraphicsView.

        Raises:
            TypeError: If the widget is not a GraphicsView.
        """
        if not isinstance(widget, GraphicsView):
            raise TypeError("TabViewWidget can only contain GraphicsViews")

        return super().setCurrentWidget(widget)

    @override
    def widget(self, index: int) -> GraphicsView:
        view = super().widget(index)

        if not isinstance(view, GraphicsView):
            raise TypeError("Current widget is not a GraphicsView")

        return view

    @override
    def deleteLater(self) -> None:
        self.blockSignals(True)
        self.clear()
        return super().deleteLater()

    @override
    def clear(self) -> None:
        self.recent_tabs.clear()

        for view in self.views():
            view.clear_scene()
            view.blockSignals(True)
            view.deleteLater()

        return super().clear()

    def view(self, index: int) -> GraphicsView:
        """
        Return the GraphicsView at the specified index.

        Raises:
            TypeError: If the widget is not a GraphicsView.
        """
        widget = super().widget(index)

        if not isinstance(widget, GraphicsView):
            raise TypeError("Widget is not a GraphicsView")

        return widget

    def views(self) -> Iterator[GraphicsView]:
        for i in range(self.count()):
            widget = super().widget(i)
            if isinstance(widget, GraphicsView):
                yield widget

    def reset_views(self) -> None:
        for view in self.views():
            view.reset_scene()

    @override
    def get_tab_label(self, index: int) -> TabLabel:
        """
        Return the TabLabel at the specified index.

        Raises:
            ValueError: If the tab label is not found or is not a TabLabel.
        """
        label = super().get_tab_label(index)
        if isinstance(label, TabLabel):
            return label

        raise ValueError("Tab label not found")

    @contextmanager
    def disabled(self) -> Generator[None]:
        self.setDisabled(True)
        yield
        self.setDisabled(False)

    @Slot(int)
    def _on_current_changed(self, index: int) -> None:
        if index == -1:
            return

        if index in self.recent_tabs:
            self.recent_tabs.move_to_end(index)
        else:
            self.recent_tabs[index] = None

        self._sync_tab_labels(index)
