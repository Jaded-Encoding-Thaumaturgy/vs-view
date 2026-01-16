"""Plugin splitter widget for managing plugin panel visibility."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QTabBar, QTabWidget, QWidget

from ...assets import IconName, IconReloadMixin

__all__ = ["PluginSplitter"]


class PluginSplitter(QSplitter, IconReloadMixin):
    """
    A horizontal splitter that manages the main content area and a collapsible plugin panel.

    Emits signals when the plugin panel becomes visible or when the active plugin tab changes,
    allowing the parent (typically the workspace) to initialize plugins only when they become truly visible.
    """

    rightPanelBecameVisible = Signal()
    """Emitted when the right panel transitions from collapsed to visible."""

    pluginTabChanged = Signal(int)
    """Emitted when the plugin tab changes (index of new tab)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        QSplitter.__init__(self, Qt.Orientation.Horizontal, parent)
        IconReloadMixin.__init__(self)

        self.plugin_tabs = QTabWidget(self)
        self.plugin_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.plugin_tabs.setDocumentMode(True)
        self.plugin_tabs.currentChanged.connect(self._on_plugin_tab_changed)

        # Sync container to match TabManager layout alignment
        self.right_corner_container = QWidget(self)
        self.right_corner_layout = QHBoxLayout(self.right_corner_container)
        self.right_corner_layout.setContentsMargins(4, 0, 4, 0)
        self.right_corner_layout.setSpacing(0)

        self.close_btn = self.make_tool_button(
            IconName.X_CIRCLE,
            "Collapse Plugin Panel",
            self.right_corner_container,
        )
        self.close_btn.clicked.connect(lambda: self.setSizes([1, 0]))
        self.right_corner_layout.addWidget(self.close_btn)

        self.plugin_tabs.setCornerWidget(self.right_corner_container, Qt.Corner.TopRightCorner)
        self.addWidget(self.plugin_tabs)

        # Start with right panel collapsed
        self.setSizes([1, 0])
        self.splitterMoved.connect(self._on_splitter_moved)
        self._right_panel_collapsed = True

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

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        right_panel_visible = self.sizes()[1] > 0

        # Only emit on transition from collapsed to visible
        if right_panel_visible and self._right_panel_collapsed:
            self.rightPanelBecameVisible.emit()

        self._right_panel_collapsed = not right_panel_visible

    def _on_plugin_tab_changed(self, index: int) -> None:
        # Only emit if right panel is visible
        if self.is_right_panel_visible:
            self.pluginTabChanged.emit(index)
