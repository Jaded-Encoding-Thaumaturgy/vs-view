from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QEasingCurve, QMimeData, QPoint, QPropertyAnimation, QSignalBlocker, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import Shiboken

from ..assets import IconReloadMixin, app_icon
from ..vsenv import gc_collect, get_policy, unregister_policy, unset_environment
from .plugins.manager import PluginManager
from .settings import ActionID, SettingsManager, ShortcutManager
from .settings.dialog import SettingsDialog
from .settings.models import WindowGeometry
from .views import StatusWidget
from .workspace import (
    BaseWorkspace,
    GenericFileWorkspace,
    LoaderWorkspace,
    PythonScriptWorkspace,
    QuickScriptWorkspace,
    VideoFileWorkspace,
)

logger = getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VS View")
        self.setWindowIcon(app_icon())

        self.settings_manager = SettingsManager()

        self._setup_ui()
        self._setup_shortcuts()
        self._restore_geometry()

    def _setup_ui(self) -> None:
        # Window Setup
        screen_geometry = self.screen().availableGeometry()
        h = round(screen_geometry.height() * 0.75)
        w = round(h * 16 / 9)
        self.resize(w, h)

        # Central Widget & Layout
        central = QWidget(self)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.setCentralWidget(central)

        # Sidebar
        sidebar = QWidget(self)
        sidebar.setFixedWidth(64)
        self.sidebar_layout = QVBoxLayout(sidebar)
        self.sidebar_layout.setContentsMargins(4, 4, 4, 4)
        self.sidebar_layout.setSpacing(8)

        # Container for dynamic page buttons
        self.nav_container = DraggableNavContainer(self)
        self.sidebar_layout.addWidget(self.nav_container)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        self.sidebar_layout.addItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        main_layout.addWidget(sidebar)

        # Content area
        self.stack = StackedWidget(self)
        main_layout.addWidget(self.stack)

        # Connect signals
        self.button_group.buttonClicked.connect(self._on_sidebar_button_clicked)

        # Menu Bar
        self.menu_bar = self.menuBar()

        self.new_menu = self.menu_bar.addMenu("New")

        self.load_script_action = QAction("Load Script...", self.new_menu)
        self.load_script_action.triggered.connect(self._on_load_script)
        self.new_menu.addAction(self.load_script_action)

        self.load_file_action = QAction("Load File...", self.new_menu)
        self.load_file_action.triggered.connect(self._on_load_file)
        self.new_menu.addAction(self.load_file_action)

        self.workspace_submenu = QMenu("Workspace", self.new_menu)
        self.new_menu.addMenu(self.workspace_submenu)

        self.script_subaction = QAction("Script", self.workspace_submenu)
        self.script_subaction.triggered.connect(lambda: self.add_workspace(PythonScriptWorkspace))
        self.workspace_submenu.addAction(self.script_subaction)

        self.file_subaction = QAction("File", self.workspace_submenu)
        self.file_subaction.triggered.connect(lambda: self.add_workspace(VideoFileWorkspace))
        self.workspace_submenu.addAction(self.file_subaction)

        self.quick_script_subaction = QAction("Quick Script", self.workspace_submenu)
        self.quick_script_subaction.triggered.connect(lambda: self.add_workspace(QuickScriptWorkspace))
        self.workspace_submenu.addAction(self.quick_script_subaction)

        self.view_menu = self.menu_bar.addMenu("View")

        self.view_tooldocks_submenu = QMenu("Tool Docks", self.view_menu)
        self.view_tooldocks_submenu.aboutToShow.connect(self._populate_tooldocks_menu)
        self.view_menu.addMenu(self.view_tooldocks_submenu)

        self.view_toolpanels_submenu = QMenu("Tool Panels", self.view_menu)
        self.view_toolpanels_submenu.aboutToShow.connect(self._populate_toolpanels_menu)
        self.view_menu.addMenu(self.view_toolpanels_submenu)

        self.settings_action = QAction("Settings", self)
        self.settings_action.triggered.connect(self._on_open_settings)
        self.menu_bar.addAction(self.settings_action)

        self.help_menu = self.menu_bar.addMenu("Help")

        self.status_widget = StatusWidget(self)
        self.statusBar().addWidget(self.status_widget, 1)
        self.status_widget.set_ready()

        if (pm := PluginManager()).loaded:
            self._init_view_tools_settings()
        else:
            pm.signals.pluginsLoaded.connect(self._init_view_tools_settings)

        # Track currently connected workspace for status bar
        self._connected_workspace: LoaderWorkspace[Any] | None = None

    def _setup_shortcuts(self) -> None:
        sm = ShortcutManager()

        sm.register_action(ActionID.LOAD_SCRIPT, self.load_script_action)
        sm.register_action(ActionID.LOAD_FILE, self.load_file_action)
        sm.register_action(ActionID.WORKSPACE_SCRIPT, self.script_subaction)
        sm.register_action(ActionID.WORKSPACE_FILE, self.file_subaction)
        sm.register_action(ActionID.WORKSPACE_QUICK_SCRIPT, self.quick_script_subaction)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_geometry()

        # We're deleting the current workspace at the end
        workspace = self.stack.currentWidget()

        for btn in self.nav_container.buttons:
            if btn.workspace is not workspace:
                btn.workspace.deleteLater()

        if workspace and Shiboken.isValid(workspace):
            workspace.deleteLater()

        with QSignalBlocker(self.settings_manager.signals):
            self.settings_manager.save_global()

        super().closeEvent(event)

    def load_new_script(self, path: Path, **vsargs: Any) -> None:
        btn = self.add_workspace(PythonScriptWorkspace)
        btn.workspace.vsargs = vsargs
        btn.workspace.load_content(path)

    def load_new_file(self, path: Path) -> None:
        btn = self.add_workspace(VideoFileWorkspace)
        btn.workspace.load_content(path)

    def add_workspace[WorkspaceT: BaseWorkspace](
        self, workspace_t: type[WorkspaceT]
    ) -> WorkspaceToolButton[WorkspaceT]:
        get_policy()

        workspace = workspace_t(self)

        self.stack.addWidget(workspace)

        btn = WorkspaceToolButton(self, workspace.title, workspace)
        btn.customContextMenuRequested.connect(lambda pos: self._show_sidebar_context_menu(pos, btn))

        self.nav_container.add_button(btn)
        self.button_group.addButton(btn)

        btn.setChecked(True)
        self.stack.setCurrentWidget(workspace)

        # Connect status bar signals for new workspace
        self._update_status_connection(workspace)

        return btn

    def delete_workspace(self, btn: WorkspaceToolButton[BaseWorkspace]) -> None:
        # Switching to another workspace before deleting the workspace and the environment,
        # otherwise we're getting a dead environment when `self.stack.removeWidget` because it triggers
        # currentChanged.connect(self.on_current_changed) in StackWidget
        if btn.isChecked() and len(buttons := self.nav_container.buttons) > 1:
            nbtn = buttons[max(0, buttons.index(btn) - 1)]
            nbtn.setChecked(True)
            nbtn.click()

        self.stack.removeWidget(btn.workspace)

        self.button_group.removeButton(btn)
        self.nav_container.remove_button(btn)

        if Shiboken.isValid(btn.workspace):
            if btn.workspace is self._connected_workspace:
                self._connected_workspace = None
            btn.workspace.deleteLater()

        btn.deleteLater()

        if not self.nav_container.buttons:
            unregister_policy()

        gc_collect()

    def _save_geometry(self) -> None:
        # For maximized windows, use normalGeometry to get the unmaximized dimensions
        # For normal windows, use pos() and size() to avoid frame coordinate issues that cause drift
        # on multi-monitor setups. (Like mine :))
        is_max = self.isMaximized()

        if is_max:
            normal_geom = self.normalGeometry()
            x, y = normal_geom.x(), normal_geom.y()
            w, h = normal_geom.width(), normal_geom.height()
        else:
            pos = self.pos()
            size = self.size()
            x, y = pos.x(), pos.y()
            w, h = size.width(), size.height()

        self.settings_manager.global_settings.window_geometry = WindowGeometry(
            x=x,
            y=y,
            width=w,
            height=h,
            is_maximized=is_max,
        )

    def _restore_geometry(self) -> None:
        geom = self.settings_manager.global_settings.window_geometry

        if geom.width is not None and geom.height is not None:
            self.resize(geom.width, geom.height)

        if geom.x is not None and geom.y is not None:
            # Validate that the position is within any available screen
            target_pos = QPoint(geom.x, geom.y)
            is_visible = False

            for screen in QApplication.screens():
                if screen.availableGeometry().contains(target_pos):
                    is_visible = True
                    break

            if is_visible:
                self.move(geom.x, geom.y)
            else:
                # Position is off-screen (monitor disconnected?), center on primary
                logger.debug("Saved position %s is off-screen, centering on primary", target_pos)
                primary = QApplication.primaryScreen()
                if primary:
                    screen_geom = primary.availableGeometry()
                    self.move(
                        screen_geom.x() + (screen_geom.width() - self.width()) // 2,
                        screen_geom.y() + (screen_geom.height() - self.height()) // 2,
                    )

        if geom.is_maximized:
            self.showMaximized()

    # SIGNALS
    def _on_load_script(self) -> None:
        btn = self.add_workspace(PythonScriptWorkspace)
        btn.workspace.load_btn.click()

    def _on_load_file(self) -> None:
        btn = self.add_workspace(VideoFileWorkspace)
        btn.workspace.load_btn.click()

    def _on_open_settings(self) -> None:
        # Get current workspace's script path if available
        if not PluginManager.settings_extracted:
            logger.warning("Plugins not loaded yet, cannot open settings dialog")
            return

        dialog = SettingsDialog(
            wk.content
            if isinstance((wk := self.stack.currentWidget()), GenericFileWorkspace) and hasattr(wk, "content")
            else None,
            self,
        )
        dialog.exec()

    def _show_sidebar_context_menu(self, pos: QPoint, btn: WorkspaceToolButton[Any]) -> None:
        menu = QMenu(self)
        actions = list[QAction]()

        if isinstance(btn.workspace, LoaderWorkspace):
            reload_action = QAction(
                "Reload",
                btn.workspace,
                shortcut=QKeySequence(ShortcutManager.get_key(ActionID.RELOAD)),
            )
            reload_action.setEnabled(btn.workspace.playback.can_reload)
            reload_action.triggered.connect(btn.workspace.reload_content)
            actions.append(reload_action)

        clear_action = QAction("Clear", self)
        clear_action.triggered.connect(lambda: self._on_clear_action(btn))
        actions.append(clear_action)

        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(lambda: self.delete_workspace(btn))
        actions.append(delete_action)

        menu.addActions(actions)
        menu.exec(btn.mapToGlobal(pos))

        for action in actions:
            action.deleteLater()

        menu.deleteLater()

    def _on_clear_action(self, btn: WorkspaceToolButton[BaseWorkspace]) -> None:
        old_index = self.nav_container.buttons.index(btn)
        workspace_type = type(btn.workspace)

        new_btn = self.add_workspace(workspace_type)

        self.nav_container.move_button(new_btn, old_index)

        self.delete_workspace(btn)

    def _update_status_connection(self, widget: QWidget) -> None:
        if self._connected_workspace is not None:
            self.status_widget.disconnect_workspace(self._connected_workspace)
            self._connected_workspace = None

        if isinstance(widget, LoaderWorkspace):
            self.status_widget.connect_workspace(widget)
            self._connected_workspace = widget

            if widget.outputs_manager.current_voutput:
                widget._emit_output_info()
        else:
            self.status_widget.clear()
            self.status_widget.set_ready()

    def _init_view_tools_settings(self) -> None:
        view_tools = self.settings_manager.global_settings.view_tools

        for dock in PluginManager.tooldocks:
            view_tools.docks.setdefault(dock.identifier, True)

        for panel in PluginManager.toolpanels:
            view_tools.panels.setdefault(panel.identifier, True)

    def _populate_plugin_menu(
        self,
        menu: QMenu,
        plugins: list[Any],
        settings: dict[str, bool],
        callback: Callable[[LoaderWorkspace[Any], int, bool], None],
        empty_text: str,
    ) -> None:
        menu.clear()

        if not PluginManager.loaded:
            menu.addAction(QAction(empty_text, menu, enabled=False))
            return

        for i, plugin in enumerate(plugins):
            action = QAction(
                plugin.display_name,
                menu,
                checkable=True,
                checked=settings.get(plugin.identifier, True),
            )

            def on_toggled(
                checked: bool,
                idx: int = i,
                identifier: str = plugin.identifier,
                workspace: QWidget = self.stack.currentWidget(),
            ) -> None:
                settings[identifier] = checked
                if isinstance(workspace, LoaderWorkspace) and workspace.plugins_loaded:
                    callback(workspace, idx, checked)

            action.toggled.connect(on_toggled)
            menu.addAction(action)

    def _populate_tooldocks_menu(self) -> None:
        self._populate_plugin_menu(
            self.view_tooldocks_submenu,
            PluginManager.tooldocks,
            self.settings_manager.global_settings.view_tools.docks,
            lambda wk, idx, checked: wk.docks[idx].setVisible(checked) if wk.dock_toggle_btn.isChecked() else None,
            "No tool docks available",
        )

    def _populate_toolpanels_menu(self) -> None:
        self._populate_plugin_menu(
            self.view_toolpanels_submenu,
            PluginManager.toolpanels,
            self.settings_manager.global_settings.view_tools.panels,
            lambda wk, idx, checked: wk.plugin_splitter.plugin_tabs.setTabVisible(idx, checked),
            "No tool panels available",
        )

    def _on_sidebar_button_clicked(self, btn: WorkspaceToolButton[Any]) -> None:
        self.stack.animate_to_widget(btn.workspace)


class StackedWidget(QStackedWidget):
    ANIMATION_DURATION = 150

    def __init__(self, /, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._last_widget: QWidget | None = self.currentWidget()
        self._animation: QPropertyAnimation | None = None
        self._overlay: QLabel | None = None
        self.animations_enabled = True

        self.currentChanged.connect(self._on_current_changed)

    def animate_to_widget(self, widget: QWidget, *, animated: bool = True) -> None:
        if (current := self.currentWidget()) is widget or widget is None:
            return

        if not animated or not self.animations_enabled:
            self.setCurrentWidget(widget)
            return

        if self._animation is not None and self._animation.state() == QPropertyAnimation.State.Running:
            self._animation.stop()
            self._finish_animation()

        # Grab screenshot of current widget
        pixmap = current.grab()

        # Switch to new widget immediately (underneath the overlay)
        self.setCurrentWidget(widget)

        # Create overlay label with the screenshot
        self._overlay = QLabel(self)
        self._overlay.setPixmap(pixmap)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        self._overlay.show()

        effect = QGraphicsOpacityEffect(self._overlay)
        self._overlay.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        # Create fade-out animation for overlay
        self._animation = QPropertyAnimation(effect, b"opacity", self)
        self._animation.setDuration(self.ANIMATION_DURATION)
        self._animation.setStartValue(1.0)
        self._animation.setEndValue(0.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._animation.finished.connect(self._finish_animation)
        self._animation.start()

    def _finish_animation(self) -> None:
        if self._overlay is not None:
            self._overlay.deleteLater()
            self._overlay = None

        self._animation = None

    def _on_current_changed(self, index: int) -> None:
        old_widget = self._last_widget

        if (
            isinstance(old_widget, BaseWorkspace)
            and Shiboken.isValid(old_widget)
            and old_widget._env
            and not old_widget._env.disposed
        ):
            logger.debug("Leaving environment %r", old_widget.env._data)
            old_widget.env.core.clear_cache()

        if index < 0:
            return

        widget = self.widget(index)

        if isinstance(widget, BaseWorkspace):
            # Only switch to environment if one already exists.
            # Accessing widget.env would create a new environment if _env is None,
            # which causes issues when switching between multiple workspaces that
            # haven't loaded content yet (e.g., Quick Script before running code).
            if widget._env and not widget._env.disposed:
                logger.debug("Switching to environment %r", widget._env._data)
                unset_environment()
                widget._env.switch()
            else:
                logger.debug("Workspace has no environment yet, just unsetting current")
                unset_environment()

        if isinstance(main_window := self.window(), MainWindow):
            main_window._update_status_connection(widget)

        self._last_widget = widget


DRAG_MIME_TYPE = "application/x-workspace-button"


class DraggableNavContainer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.buttons = list[WorkspaceToolButton[BaseWorkspace]]()
        self._drop_index = -1
        self.setAcceptDrops(True)

        # Drop indicator line
        self._drop_indicator = QFrame(self)
        self._drop_indicator.setFrameShape(QFrame.Shape.HLine)
        accent = self.palette().color(QPalette.ColorRole.Accent).name()
        self._drop_indicator.setStyleSheet(f"background-color: {accent}; min-height: 2px; max-height: 2px;")
        self._drop_indicator.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(DRAG_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(DRAG_MIME_TYPE):
            self._drop_index = self._get_drop_index(int(event.position().y()))
            self._update_drop_indicator(self._drop_index)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._drop_indicator.hide()
        self._drop_index = -1

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_indicator.hide()

        if not event.mimeData().hasFormat(DRAG_MIME_TYPE):
            return

        source = cast(WorkspaceToolButton[Any], event.source())

        if source not in self.buttons:
            return

        old_index = self.buttons.index(source)
        new_index = self._drop_index if self._drop_index <= old_index else self._drop_index - 1

        if 0 <= new_index < len(self.buttons) and new_index != old_index:
            self.move_button(source, new_index)

        self._drop_index = -1
        event.acceptProposedAction()

    def add_button(self, btn: WorkspaceToolButton[Any]) -> None:
        self.buttons.append(btn)
        self._layout.addWidget(btn)

    def remove_button(self, btn: WorkspaceToolButton[Any]) -> None:
        if btn in self.buttons:
            self.buttons.remove(btn)
            self._layout.removeWidget(btn)

    def move_button(self, btn: WorkspaceToolButton[Any], new_index: int) -> None:
        if btn not in self.buttons or self.buttons.index(btn) == new_index:
            return

        self.buttons.remove(btn)
        self.buttons.insert(new_index, btn)
        self._layout.removeWidget(btn)
        self._layout.insertWidget(new_index, btn)

    def _get_drop_index(self, pos_y: int) -> int:
        for i, btn in enumerate(self.buttons):
            if pos_y < btn.y() + btn.height() // 2:
                return i
        return len(self.buttons)

    def _update_drop_indicator(self, index: int) -> None:
        if index < 0 or not self.buttons:
            self._drop_indicator.hide()
            return

        target = self.buttons[min(index, len(self.buttons) - 1)]
        y_pos = target.y() - 4 if index < len(self.buttons) else target.y() + target.height() + 2

        self._drop_indicator.setGeometry(0, y_pos, self.width(), 2)
        self._drop_indicator.show()
        self._drop_indicator.raise_()


class WorkspaceToolButton[WorkspaceT: BaseWorkspace](QToolButton, IconReloadMixin):
    ICON_SIZE = QSize(30, 30)

    def __init__(self, /, parent: QWidget, title: str, workspace: WorkspaceT) -> None:
        super().__init__(parent)
        self.workspace: WorkspaceT = workspace

        self.setIcon(self._make_icon(workspace))
        self.setIconSize(self.ICON_SIZE)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolTip(title)
        self.setFixedSize(56, 56)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.register_icon_callback(lambda: self.setIcon(self._make_icon(workspace)))

        # Drag tracking
        self._drag_start_pos: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start_pos).manhattanLength() >= 10
        ):
            self._drag_start_pos = None
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setData(DRAG_MIME_TYPE, b"")
            drag.setMimeData(mime_data)
            drag.setPixmap(self.grab())
            drag.setHotSpot(QPoint(self.width() // 2, self.height() // 2))
            drag.exec(Qt.DropAction.MoveAction)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _make_icon(self, workspace: BaseWorkspace) -> QIcon:
        palette = self.palette()

        return self.make_icon(
            {
                (QIcon.Mode.Normal, QIcon.State.Off): (
                    workspace.icon,
                    palette.color(QPalette.ColorRole.Accent),
                ),
                (QIcon.Mode.Normal, QIcon.State.On): (
                    workspace.icon,
                    palette.color(QPalette.ColorRole.Mid),
                ),
            },
            size=self.ICON_SIZE,
        )
