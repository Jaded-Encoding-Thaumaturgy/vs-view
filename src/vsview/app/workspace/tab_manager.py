from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import partial
from logging import getLogger
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ...assets import IconName, IconReloadMixin
from ...vsenv import run_in_loop
from ..outputs import VideoOutput
from ..settings import ActionID, ShortcutManager
from ..views import GraphicsView
from ..views.tab import TabLabel, TabViewWidget

logger = getLogger(__name__)


class TabManager(QWidget, IconReloadMixin):
    """Manages the video output tabs and their synchronization state."""

    # Signals
    tabChanged = Signal(int)  # index

    # Status bar signals
    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.current_layout = QVBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(0)

        # Sync controls container
        self.sync_container = QWidget(self)
        self.sync_layout = QHBoxLayout(self.sync_container)
        self.sync_layout.setContentsMargins(4, 0, 4, 0)
        self.sync_layout.setSpacing(2)

        icon_states: dict[Any, Any] = {
            (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ButtonText,
            (QIcon.Mode.Normal, QIcon.State.On): QPalette.ColorRole.Base,
            (QIcon.Mode.Disabled, QIcon.State.Off): (QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
            (QIcon.Mode.Disabled, QIcon.State.On): (QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base),
        }
        self.sync_playhead_btn = self.make_tool_button(
            IconName.LINK,
            "Sync Playhead",
            self,
            checkable=True,
            checked=True,
            icon_states=icon_states,
        )
        self.sync_zoom_btn = self.make_tool_button(
            IconName.MAGNIFYING_GLASS,
            "Sync Zoom",
            self,
            checkable=True,
            checked=True,
            icon_states=icon_states,
        )
        self.sync_scroll_btn = self.make_tool_button(
            IconName.ARROWS_OUT_CARDINAL,
            "Sync Scroll",
            self,
            checkable=True,
            checked=True,
            icon_states=icon_states,
        )
        self.autofit_btn = self.make_tool_button(
            IconName.FRAME_CORNERS,
            "Autofit All Views",
            self,
            checkable=True,
            checked=False,
            icon_states=icon_states,
        )
        self.sync_zoom_btn.toggled.connect(self._on_sync_zoom_changed)
        self.autofit_btn.toggled.connect(self._on_global_autofit_changed)

        self.sync_layout.addWidget(self.sync_playhead_btn)
        self.sync_layout.addWidget(self.sync_zoom_btn)
        self.sync_layout.addWidget(self.sync_scroll_btn)
        self.sync_layout.addWidget(self.autofit_btn)

        # The actual tabs widget
        self.tabs = TabViewWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.current_layout.addWidget(self.tabs)

        self.tabs.setCornerWidget(self.sync_container, Qt.Corner.TopRightCorner)

        self.disable_switch = True

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        sm = ShortcutManager()
        sm.register_shortcut(ActionID.SYNC_PLAYHEAD, self.sync_playhead_btn.toggle, self)
        sm.register_shortcut(ActionID.SYNC_ZOOM, self.sync_zoom_btn.toggle, self)
        sm.register_shortcut(ActionID.SYNC_SCROLL, self.sync_scroll_btn.toggle, self)
        sm.register_shortcut(ActionID.AUTOFIT_ALL_VIEWS, self.autofit_btn.toggle, self)

    @property
    def current_view(self) -> GraphicsView:
        return self.tabs.currentWidget()

    @property
    def previous_view(self) -> GraphicsView:
        return self.tabs.widget(self.tabs.previous_tab_index)

    @property
    def is_sync_playhead_enabled(self) -> bool:
        return self.sync_playhead_btn.isChecked()

    @property
    def is_sync_zoom_enabled(self) -> bool:
        return self.sync_zoom_btn.isChecked()

    @property
    def is_sync_scroll_enabled(self) -> bool:
        return self.sync_scroll_btn.isChecked()

    def deleteLater(self) -> None:
        self.tabs.blockSignals(True)
        self.tabs.deleteLater()
        return super().deleteLater()

    @run_in_loop(return_future=False)
    def create_tabs(self, video_outputs: Sequence[VideoOutput], frame: int = 0, enabled: bool = True) -> TabViewWidget:
        new_tabs = TabViewWidget(self)
        new_tabs.setDocumentMode(True)

        for voutput in video_outputs:
            view = GraphicsView(self)
            view.zoomChanged.connect(self._on_zoom_changed)
            view.autofitChanged.connect(partial(self._on_autofit_changed, view))
            view.statusSavingImageStarted.connect(self.statusLoadingStarted.emit)
            view.statusSavingImageFinished.connect(self.statusLoadingFinished.emit)
            view.last_frame = frame

            tab_label = TabLabel(voutput.vs_name, voutput.vs_index, new_tabs)

            # Inherit global autofit state
            with QSignalBlocker(view):
                view.set_autofit(self.autofit_btn.isChecked())

            # Add tab with empty text (label widget replaces it)
            tab_i = new_tabs.addTab(view, "")
            new_tabs.tabBar().setTabButton(tab_i, new_tabs.tabBar().ButtonPosition.LeftSide, tab_label)

        if new_tabs.count() <= 1:
            new_tabs.tabBar().hide()
        else:
            new_tabs.tabBar().show()

        new_tabs.setEnabled(enabled)

        return new_tabs

    @run_in_loop(return_future=False)
    def swap_tabs(self, new_tabs: TabViewWidget, tab_index: int) -> None:
        old_tabs = self.tabs

        new_tabs.setCornerWidget(self.sync_container, Qt.Corner.TopRightCorner)
        self.sync_container.show()

        self.current_layout.replaceWidget(old_tabs, new_tabs)
        new_tabs.show()

        self.tabs = new_tabs

        self.tabs.currentChanged.connect(self._on_tab_changed)

        old_tabs.deleteLater()

        if self.tabs.count() > 0:
            target_index = tab_index

            if target_index != self.tabs.currentIndex():
                self.tabs.setCurrentIndex(target_index)
            else:
                # setCurrentIndex won't emit if index is already current, so call handler manually
                self._on_tab_changed(target_index)

    def switch_tab(self, index: int) -> None:
        if self.disable_switch:
            logger.warning("Switching tabs is disabled")
            return
        self.tabs.setCurrentIndex(index)

    @run_in_loop
    def update_current_view(self, image: QImage, skip_adjustments: bool = False) -> None:
        """Update the view with a new rendered frame."""

        if self.tabs.currentIndex() == -1:
            return

        try:
            self.current_view.pixmap_item.setPixmap(
                QPixmap.fromImage(image, Qt.ImageConversionFlag.NoFormatConversion),
            )
        except ValueError:
            logger.warning("Failed to update view with new frame")
            return

        if skip_adjustments:
            return

        self.current_view.setSceneRect(self.current_view.pixmap_item.boundingRect())

        if self.current_view.autofit:
            self.current_view.set_zoom(0)

    @contextmanager
    def clear_voutputs_on_fail(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.tabs.clear()
            raise

    # SIGNALS
    def _on_tab_changed(self, index: int) -> None:
        if index < 0:
            return

        new_view = self.tabs.view(index)

        if (
            self.sync_scroll_btn.isChecked()
            and self.previous_view is not new_view
            and not self.previous_view.autofit
            and not self.previous_view.pixmap_item.pixmap().isNull()
        ):
            prev_h_bar = self.previous_view.horizontalScrollBar()
            prev_v_bar = self.previous_view.verticalScrollBar()

            h_ratio = prev_h_bar.value() / prev_h_bar.maximum() if prev_h_bar.maximum() > 0 else 0.0
            v_ratio = prev_v_bar.value() / prev_v_bar.maximum() if prev_v_bar.maximum() > 0 else 0.0

            # Apply scroll immediately
            new_h_bar = new_view.horizontalScrollBar()
            new_v_bar = new_view.verticalScrollBar()

            if new_h_bar.maximum() > 0:
                new_h_bar.setValue(int(h_ratio * new_h_bar.maximum()))
            if new_v_bar.maximum() > 0:
                new_v_bar.setValue(int(v_ratio * new_v_bar.maximum()))

        self.tabChanged.emit(index)

    def _on_zoom_changed(self, zoom: float) -> None:
        """Handle zoom change events from GraphicsView widgets."""
        if zoom not in self._settings_manager.global_settings.view.zoom_factors:
            raise ValueError(f"Invalid zoom factor: {zoom}")

        if (idx := self.tabs.indexOf(self.current_view)) >= 0:
            self.tabs.get_tab_label(idx).zoom = zoom

        if not self.is_sync_zoom_enabled:
            return

        for i, view in enumerate(self.tabs.views()):
            if view is not self.current_view and not view.autofit:
                with QSignalBlocker(view):
                    view.set_zoom(zoom)
                    view.slider.setValue(view._zoom_to_slider(zoom))

                self.tabs.get_tab_label(i).zoom = zoom

    def _on_sync_zoom_changed(self, checked: bool) -> None:
        if checked:
            self._on_zoom_changed(self.current_view.current_zoom)

    def _on_global_autofit_changed(self, enabled: bool) -> None:
        for i, view in enumerate(self.tabs.views()):
            with QSignalBlocker(view):
                view.set_autofit(enabled)

                if enabled:
                    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    if view is self.current_view:
                        view.set_zoom(0)
                else:
                    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                    view.set_zoom(view._slider_to_zoom(view.slider.value()))

            self.tabs.get_tab_label(i).zoom = 0 if enabled else view.current_zoom

    def _on_autofit_changed(self, view: GraphicsView, enabled: bool) -> None:
        if (idx := self.tabs.indexOf(view)) >= 0:
            self.tabs.get_tab_label(idx).zoom = 0 if enabled else view.current_zoom
