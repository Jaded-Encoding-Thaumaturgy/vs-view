"""Plugin splitter widget for managing plugin panel visibility."""

from collections.abc import Sequence
from typing import override

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QSplitter, QTabBar, QTabWidget, QWidget

from ...assets import IconName
from ..icon import IconReloadMixin

__all__ = ["PluginSplitter"]


class PluginSplitter(QSplitter, IconReloadMixin):
    """
    A horizontal splitter that manages the main content area and a collapsible plugin panel.
    """

    rightPanelVisibilityChanged = Signal(bool)
    """Emitted on any visibility transition. True = visible, False = collapsed."""

    pluginTabChanged = Signal(int, int)  # new_index, old_index
    """Emitted when the plugin tab changes (index of new tab)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        QSplitter.__init__(self, Qt.Orientation.Horizontal, parent)
        IconReloadMixin.__init__(self)

        self.plugin_tabs = QTabWidget(self)
        self.plugin_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.plugin_tabs.setDocumentMode(True)
        self.plugin_tabs.currentChanged.connect(self._on_plugin_tab_changed)
        self.old_tab_index = self.plugin_tabs.currentIndex()

        # Sync container to match TabManager layout alignment
        self.right_corner_container = QWidget(self)
        self.right_corner_layout = QHBoxLayout(self.right_corner_container)
        self.right_corner_layout.setContentsMargins(4, 0, 4, 0)
        self.right_corner_layout.setSpacing(0)

        self.close_btn = self.make_tool_button(IconName.X_CIRCLE, "Collapse Plugin Panel", self.right_corner_container)
        self.close_btn.clicked.connect(lambda: self.setSizes([1, 0]))
        self.right_corner_layout.addWidget(self.close_btn)

        self.plugin_tabs.setCornerWidget(self.right_corner_container, Qt.Corner.TopRightCorner)
        self.addWidget(self.plugin_tabs)

        # Start with right panel collapsed
        self.right_panel_collapsed = True
        self.setSizes([1, 0])
        self.last_sizes = [1, 0]
        self.splitterMoved.connect(lambda *_: self.setSizes(self.sizes()))  # for manual drag sync

    @override
    def setSizes(self, sizes: Sequence[int]) -> None:
        was_collapsed = self.right_panel_collapsed
        super().setSizes(sizes)
        self.right_panel_collapsed = sizes[1] == 0

        if was_collapsed and not self.right_panel_collapsed:
            self.rightPanelVisibilityChanged.emit(True)
        elif not was_collapsed and self.right_panel_collapsed:
            self.rightPanelVisibilityChanged.emit(False)

    @property
    def is_right_panel_visible(self) -> bool:
        return self.sizes()[1] > 0

    def insert_main_widget(self, widget: QWidget) -> None:
        self.insertWidget(0, widget)
        self.setSizes([1, 0])  # Reset sizes after insertion

    def add_plugin(self, widget: QWidget, title: str) -> None:
        index = self.plugin_tabs.addTab(widget, "")

        # Use a custom label widget to match TabLabel's vertical margins (4, 4, 4, 4)
        # This ensures the tab bar height matches TabManager's tab bar.
        label_widget = QWidget(self)
        layout = QHBoxLayout(label_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        label = QLabel(title, label_widget)
        layout.addWidget(label)

        self.plugin_tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.LeftSide, label_widget)

    @Slot(bool)
    def toggle_right_panel(self, visible: bool) -> None:
        if visible:
            if self.last_sizes == [1, 0]:
                self.setSizes([75, 25])
            else:
                self.setSizes(self.last_sizes)
        else:
            self.last_sizes = self.sizes()
            self.setSizes([1, 0])

    @Slot(int)
    def _on_plugin_tab_changed(self, index: int) -> None:
        # Only emit if right panel is visible
        if self.is_right_panel_visible:
            self.pluginTabChanged.emit(index, self.old_tab_index)
            self.old_tab_index = index


class PluginDock(QDockWidget):
    """A dock widget for a plugin."""

    def __init__(self, title: str, identifier: str, parent: QWidget) -> None:
        super().__init__(title, parent)
        self.setObjectName(identifier)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setVisible(False)

        self.truly_visible = False
