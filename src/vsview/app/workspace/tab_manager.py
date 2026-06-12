from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from enum import IntEnum
from functools import partial
from itertools import cycle
from logging import getLogger
from typing import TYPE_CHECKING, Self, overload, override

from jetpytools import fallback
from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget
from vapoursynth import VideoFrame

from ...assets import IconName
from ...vsenv import run_in_loop
from ..icon import IconReloadMixin
from ..plugins.api import PluginAPI
from ..settings import ActionID, SettingsManager, ShortcutManager
from ..views import GraphicsView
from ..views.tab import TabLabel, TabViewWidget

if TYPE_CHECKING:
    from ..outputs import VideoOutput

logger = getLogger(__name__)


class PlayHeadToolButton(QToolButton, IconReloadMixin):
    """Four-state QToolButton for syncing play head."""

    class State(IntEnum):
        UNLINK = 0, IconName.UNLINK, "Unlink"
        LINK_ADAPT = 1, IconName.LINK, "Adaptive link (uses current timeline mode: time or frame)"
        LINK_TIME = 2, IconName.LINK_2, "Link by time"
        LINK_FRAME = 3, IconName.LINK_3, "Link by frame"

        icon_name: IconName
        description: str

        def __new__(cls, value: int, icon_name: IconName, description: str) -> Self:
            obj = int.__new__(cls, value)
            obj._value_ = value
            obj.icon_name = icon_name
            obj.description = description
            return obj

    stateChanged = Signal(State)
    rightClicked = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.setIconSize(QSize(20, 20))

        # Start from LINK_ADAPT
        modes = deque(PlayHeadToolButton.State)
        modes.rotate(-1)
        self.modes_order = tuple(modes)

        self._state_cycle = cycle(self.modes_order)
        self._icons = list[QIcon]()
        self.state = next(self._state_cycle)

        self.reload_icons()
        self.register_icon_callback(self.reload_icons)

        self.clicked.connect(self.set_state)
        self.rightClicked.connect(lambda clicked: self.set_state(clicked, (self.state - 1) % len(self.State)))
        self._update_tooltip()

    @override
    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(True)
        return super().mouseReleaseEvent(e)

    def set_state(self, clicked: bool | None = None, state: int | None = None) -> None:
        if state is None:
            state = next(self._state_cycle)
        elif state is not None and state not in PlayHeadToolButton.State:
            logger.warning("Unknown play head state %r", state)
            state = PlayHeadToolButton.State.LINK_ADAPT

        while self.state != state:
            self.state = next(self._state_cycle)

        self.setChecked(bool(self.state))
        self.setIcon(self._icons[self.state])
        self._update_tooltip()
        self.stateChanged.emit(self.state)

    def reload_icons(self) -> None:
        self._icons.clear()

        for icon_name in (state.icon_name for state in PlayHeadToolButton.State):
            icon_desc = {
                mode_state: (icon_name, self.palette().color(*color_group_role))
                for mode_state, color_group_role in self.DEFAULT_ICON_STATES.items()
            }

            self._icons.append(self.make_icon(icon_desc))

        self.setIcon(self._icons[self.state])

    def _update_tooltip(self) -> None:
        lines = [
            "Sync playhead between tabs.",
            f"Current mode: {self.state.description}",
            "Click to cycle modes:",
        ]

        for mode in self.modes_order:
            prefix = "[x]" if self.state == mode else "[ ]"
            lines.append(f"{prefix} {mode.description}")
        self.setToolTip("\n".join(lines))


class TabManager(QWidget, IconReloadMixin):
    """Manages the video output tabs and their synchronization state."""

    # Signals
    tabChanged = Signal(int)  # index
    sarTransformed = Signal(float)  # sar value

    # Status bar signals
    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)

        self.api = api

        self.current_layout = QVBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(0)

        # Sync controls container
        self.sync_container = QWidget(self)
        self.sync_layout = QHBoxLayout(self.sync_container)
        self.sync_layout.setContentsMargins(4, 0, 4, 0)
        self.sync_layout.setSpacing(2)

        self.sync_playhead_btn = PlayHeadToolButton(self)
        self.sync_zoom_btn = self.make_tool_button(
            IconName.MAGNIFYING_GLASS,
            "Link zoom between tabs.\nWhen enabled, zooming in one tab applies the same zoom level to all tabs.",
            self,
            checkable=True,
            checked=True,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.sync_scroll_btn = self.make_tool_button(
            IconName.ARROWS_OUT_CARDINAL,
            "Link pan/scroll between tabs.\n"
            "When enabled, moving the view in one tab updates the same position in all tabs.",
            self,
            checkable=True,
            checked=True,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.autofit_btn = self.make_tool_button(
            IconName.FRAME_CORNERS,
            "Auto-fit all tabs to the viewport.\n"
            "When enabled, each tab automatically fits content to the available view size.",
            self,
            checkable=True,
            checked=False,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.toggle_tooldock_btn = self.make_tool_button(
            IconName.SIDEBAR_LEFT,
            "Show or hide the Plugin Tool Dock for the current workspace.",
            self,
            checkable=True,
            checked=False,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.toggle_toolpanel_btn = self.make_tool_button(
            IconName.SIDEBAR_RIGHT,
            "Show or hide the Plugin Tool Panel for the current workspace.",
            self,
            checkable=True,
            checked=False,
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.sync_zoom_btn.toggled.connect(self._on_sync_zoom_changed)
        self.autofit_btn.toggled.connect(self._on_global_autofit_changed)

        self.sync_layout.addWidget(self.toggle_tooldock_btn)
        self.sync_layout.addWidget(self.sync_playhead_btn)
        self.sync_layout.addWidget(self.sync_zoom_btn)
        self.sync_layout.addWidget(self.sync_scroll_btn)
        self.sync_layout.addWidget(self.autofit_btn)
        self.sync_layout.addWidget(self.toggle_toolpanel_btn)

        # The actual tabs widget
        self.tabs = TabViewWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.current_layout.addWidget(self.tabs)

        self.tabs.setCornerWidget(self.sync_container, Qt.Corner.TopRightCorner)

        self.disable_switch = True

        self._setup_shortcuts()

        SettingsManager.signals.globalChanged.connect(self.set_tab_visiblity)

    def _setup_shortcuts(self) -> None:
        sm = ShortcutManager()
        sm.register_shortcut(ActionID.TOGGLE_PLUGIN_DOCK, self.toggle_tooldock_btn.toggle, self)
        sm.register_shortcut(ActionID.SYNC_PLAYHEAD, self.sync_playhead_btn.set_state, self)
        sm.register_shortcut(ActionID.SYNC_ZOOM, self.sync_zoom_btn.toggle, self)
        sm.register_shortcut(ActionID.SYNC_SCROLL, self.sync_scroll_btn.toggle, self)
        sm.register_shortcut(ActionID.AUTOFIT_ALL_VIEWS, self.autofit_btn.toggle, self)
        sm.register_shortcut(ActionID.TOGGLE_PLUGIN_PANEL, self.toggle_toolpanel_btn.toggle, self)

    @property
    def current_view(self) -> GraphicsView:
        return self.tabs.currentWidget()

    @property
    def previous_view(self) -> GraphicsView:
        return self.tabs.widget(self.tabs.previous_tab_index)

    @property
    def sync_playhead_state(self) -> PlayHeadToolButton.State:
        return self.sync_playhead_btn.state

    @property
    def is_sync_zoom_enabled(self) -> bool:
        return self.sync_zoom_btn.isChecked()

    @property
    def is_sync_scroll_enabled(self) -> bool:
        return self.sync_scroll_btn.isChecked()

    @override
    def deleteLater(self) -> None:
        self.tabs.blockSignals(True)
        self.tabs.deleteLater()
        return super().deleteLater()

    @run_in_loop(return_future=False)
    def create_tabs(self, video_outputs: Sequence[VideoOutput], enabled: bool = True) -> TabViewWidget:
        new_tabs = TabViewWidget(self)
        new_tabs.setDocumentMode(True)

        for voutput in video_outputs:
            view = GraphicsView(self)
            view.zoomChanged.connect(self._on_zoom_changed)
            view.autofitChanged.connect(partial(self._on_autofit_changed, view))
            view.contextMenuRequested.connect(self.api._on_view_context_menu)
            view.mouseMoved.connect(self.api._on_view_mouse_moved)
            view.mousePressed.connect(self.api._on_view_mouse_pressed)
            view.mouseReleased.connect(self.api._on_view_mouse_released)
            view.rectSelectionChanged.connect(self.api._on_view_rect_selection_changed)
            view.rectSelectionFinished.connect(self.api._on_view_rect_selection_finished)
            view.keyPressed.connect(self.api._on_view_key_press)
            view.keyReleased.connect(self.api._on_view_key_release)
            view.statusSavingImageStarted.connect(self.statusLoadingStarted.emit)
            view.statusSavingImageFinished.connect(self.statusLoadingFinished.emit)
            view.displayTransformChanged.connect(lambda transform: self.sarTransformed.emit(transform.m11()))
            view.set_hdr_enabled(voutput.packer.hdr)

            tab_label = TabLabel(voutput.vs_name, voutput.vs_index, new_tabs)

            # Add tab with empty text (label widget replaces it)
            tab_i = new_tabs.addTab(view, "")
            new_tabs.tabBar().setTabButton(tab_i, new_tabs.tabBar().ButtonPosition.LeftSide, tab_label)

        if new_tabs.count() <= 1:
            self.sync_playhead_btn.setDisabled(True)
            self.sync_zoom_btn.setDisabled(True)
            self.sync_scroll_btn.setDisabled(True)
            self.autofit_btn.setDisabled(True)
        else:
            self.sync_playhead_btn.setEnabled(True)
            self.sync_zoom_btn.setEnabled(True)
            self.sync_scroll_btn.setEnabled(True)
            self.autofit_btn.setEnabled(True)

        self.set_tab_visiblity(new_tabs)

        return new_tabs

    @run_in_loop(return_future=False)
    def swap_tabs(self, new_tabs: TabViewWidget, tab_index: int) -> None:
        old_tabs = self.tabs

        new_tabs.setCornerWidget(self.sync_container, Qt.Corner.TopRightCorner)
        self.sync_container.show()

        new_tabs.recent_tabs[tab_index] = None
        new_tabs.setCurrentIndex(tab_index)
        new_tabs.currentChanged.connect(new_tabs._on_current_changed)
        new_tabs.currentChanged.connect(self._on_tab_changed)

        self.current_layout.replaceWidget(old_tabs, new_tabs)
        new_tabs.show()

        self.tabs = new_tabs

        old_tabs.deleteLater()

    @overload
    def switch_tab(self, index: int, /) -> None: ...
    @overload
    def switch_tab(self, *, delta: int) -> None: ...
    def switch_tab(self, index: int | None = None, delta: int = 0) -> None:
        if self.disable_switch:
            logger.warning("Switching tabs is disabled")
            return

        self.tabs.setCurrentIndex(fallback(index, self.tabs.currentIndex() + delta))

    def set_tab_visiblity(self, tabs: TabViewWidget | None = None) -> None:
        tabs = tabs or self.tabs

        match SettingsManager.global_settings.appearance.tab_bar:
            case Qt.CheckState.Unchecked:
                tabs.tabBar().hide()
            case Qt.CheckState.Checked:
                tabs.tabBar().show()
            case Qt.CheckState.PartiallyChecked:
                if tabs.count() <= 1:
                    tabs.tabBar().hide()
                else:
                    tabs.tabBar().show()

    @run_in_loop(return_future=False)
    def update_current_view(
        self,
        image: QImage | QPixmap,
        backing_frame: VideoFrame | None = None,
        sar: float | None = None,
    ) -> None:
        """Update the view with a new rendered frame."""

        if self.tabs.currentIndex() == -1:
            if backing_frame:
                backing_frame.close()
            return

        try:
            pixmap = (
                image
                if isinstance(image, QPixmap)
                else QPixmap.fromImage(image, Qt.ImageConversionFlag.NoFormatConversion)
            )
            self.current_view.set_pixmap(pixmap, image if isinstance(image, QImage) else None)
            self.current_view.set_sar(sar)
        finally:
            if backing_frame:
                backing_frame.close()

    # SIGNALS
    def _on_tab_changed(self, index: int) -> None:
        if index < 0:
            return

        new_view = self.tabs.view(index)
        prev_view = self.previous_view

        if (
            self.sync_scroll_btn.isChecked()
            and prev_view is not new_view
            and not prev_view.autofit
            and not prev_view.pixmap_item.pixmap().isNull()
        ):
            # If the new view is currently empty, give it a dummy pixmap so update_center works
            if new_view.pixmap_item.pixmap().isNull():
                new_view.set_pixmap(QPixmap(prev_view.pixmap_item.pixmap().size()))

            QTimer.singleShot(0, lambda: new_view.update_center(prev_view))

        self.tabChanged.emit(index)

    def _on_zoom_changed(self, zoom: float) -> None:
        """Handle zoom change events from GraphicsView widgets."""
        if (idx := self.tabs.indexOf(self.current_view)) >= 0:
            self.tabs.get_tab_label(idx).zoom = zoom

        if not self.is_sync_zoom_enabled:
            return

        for i, view in enumerate(self.tabs.views()):
            if view is not self.current_view and not view.autofit:
                with QSignalBlocker(view), QSignalBlocker(view.slider):
                    view.set_zoom(zoom, animated=False)
                    view.slider.setValue(view.zoom_to_slider(zoom))

                self.tabs.get_tab_label(i).zoom = zoom

    def _on_sync_zoom_changed(self, checked: bool) -> None:
        if checked:
            self._on_zoom_changed(self.current_view.current_zoom)

    def _on_global_autofit_changed(self, enabled: bool, under_reload: bool = False) -> None:
        for i, view in enumerate(self.tabs.views()):
            with QSignalBlocker(view):
                if under_reload and not enabled and view.autofit:
                    self.tabs.get_tab_label(i).zoom = 0
                    continue

                view.set_autofit(enabled, animated=not under_reload and view is self.current_view)

            self.tabs.get_tab_label(i).zoom = 0 if enabled else view.current_zoom

    def _on_autofit_changed(self, view: GraphicsView, enabled: bool) -> None:
        if (idx := self.tabs.indexOf(view)) >= 0:
            self.tabs.get_tab_label(idx).zoom = 0 if enabled else view.current_zoom
