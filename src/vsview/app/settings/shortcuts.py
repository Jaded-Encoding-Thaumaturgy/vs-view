"""Shortcut manager for hot-reloadable keyboard shortcuts."""

from collections.abc import Callable
from logging import getLogger
from typing import Any
from weakref import WeakSet

from jetpytools import Singleton, inject_self
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget
from shiboken6 import Shiboken

from .manager import SettingsManager
from .models import ActionID

logger = getLogger(__name__)


class ShortcutManager(Singleton):
    """
    Manages application shortcuts with hot-reload support.

    This class maintains a registry of QAction and QShortcut objects keyed by ActionID.
    When settings change (via global_changed signal), all shortcuts are automatically updated.

    Usage:
        ```python
        # For menu actions (QAction already exists)
        shortcut_manager.register_action(ActionID.LOAD_SCRIPT, my_action)

        # For standalone shortcuts (creates QShortcut)
        shortcut = shortcut_manager.register_shortcut(ActionID.PLAY_PAUSE, callback, parent_widget)
        ```
    """

    def __init__(self) -> None:
        self._settings_manager = SettingsManager()

        # Storage for registered shortcuts
        self._actions = {aid: WeakSet[QAction]() for aid in ActionID}
        self._shortcuts = {aid: WeakSet[QShortcut]() for aid in ActionID}

        # Connect to settings change signal for hot reload
        self._settings_manager.signals.globalChanged.connect(self._on_settings_changed)

        logger.debug("ShortcutManager initialized")

    @inject_self
    def register_action(self, action_id: ActionID, action: QAction) -> None:
        """
        Register a QAction for shortcut management.

        Args:
            action_id: The ActionID for this shortcut.
            action: The QAction to manage.
        """
        self._actions[action_id].add(action)
        self._update_action(action_id, action)
        logger.debug("Registered action for %s: %r", action_id, action.text())

    @inject_self
    def register_shortcut(self, action_id: ActionID, callback: Callable[[], Any], context: QWidget) -> QShortcut:
        """
        Create and register a QShortcut for shortcut management.

        Args:
            action_id: The ActionID for this shortcut.
            callback: The function to call when the shortcut is activated.
            context: The parent widget that determines shortcut scope.

        Returns:
            The created QShortcut instance.
        """
        shortcut = QShortcut(context)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)

        self._shortcuts[action_id].add(shortcut)
        self._update_shortcut(action_id, shortcut)

        logger.debug("Registered shortcut for %s in context %r", action_id, context.__class__.__name__)
        return shortcut

    @inject_self
    def unregister_shortcut(self, action_id: ActionID, shortcut: QShortcut) -> None:
        """Unregister a previously registered shortcut."""
        self._shortcuts[action_id].discard(shortcut)
        logger.debug("Unregistered shortcut for %s", action_id)

    @inject_self
    def get_key(self, action_id: ActionID) -> str:
        """Get the current key sequence for an action from settings."""
        return self._settings_manager.global_settings.get_key(action_id)

    def _update_action(self, action_id: ActionID, action: QAction) -> None:
        if Shiboken.isValid(action):
            action.setShortcut(self.get_key(action_id))
        else:
            del action

    def _update_shortcut(self, action_id: ActionID, shortcut: QShortcut) -> None:
        if Shiboken.isValid(shortcut):
            shortcut.setKey(QKeySequence(self.get_key(action_id)))
        else:
            del shortcut

    def _on_settings_changed(self) -> None:
        logger.info("Hot-reloading shortcuts...")

        for action_id in ActionID:
            for action in self._actions[action_id]:
                self._update_action(action_id, action)

            for shortcut in self._shortcuts[action_id]:
                self._update_shortcut(action_id, shortcut)

        logger.info("Shortcuts hot-reloaded")
