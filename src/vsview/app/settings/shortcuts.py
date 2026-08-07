"""Shortcut manager for hot-reloadable keyboard shortcuts."""

import operator
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from logging import getLogger
from typing import Any, Literal

from jetpytools import Singleton, inject_self
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

from ..utils import QObjectSet
from .action import ActionDefinition, ActionID
from .manager import SettingsManager
from .models import ShortcutConfig

logger = getLogger(__name__)


class ShortcutManager(Singleton):
    """
    Manages application shortcuts with hot-reload support.

    This class maintains a registry of QAction and QShortcut objects keyed by ActionID.
    When settings change (via global_changed signal), all shortcuts are automatically updated.

    Usage:
        ```python
        # For menu actions (QAction already exists)
        ShortcutManager.register_action(ActionID.LOAD_SCRIPT, my_action)

        # For standalone shortcuts (creates QShortcut)
        shortcut = ShortcutManager.register_shortcut(ActionID.PLAY_PAUSE, callback, parent_widget)
        ```
    """

    SCOPE_HIERARCHY: Mapping[Qt.ShortcutContext, Literal[0, 1, 2, 3]] = {
        Qt.ShortcutContext.WidgetShortcut: 0,  # Most specific
        Qt.ShortcutContext.WidgetWithChildrenShortcut: 1,
        Qt.ShortcutContext.WindowShortcut: 2,
        Qt.ShortcutContext.ApplicationShortcut: 3,  # Most general
    }

    def __init__(self) -> None:
        # Storage for registered shortcuts
        self._actions = defaultdict[str, QObjectSet[QAction]](QObjectSet)
        self._shortcuts = defaultdict[str, QObjectSet[QShortcut]](QObjectSet)
        self._definitions = dict[str, ActionDefinition]()

        # Pre-register all core actions
        self.register_definitions(aid.definition for aid in ActionID)

        # Connect to settings change signal for hot reload
        SettingsManager.signals.globalChanged.connect(self._on_settings_changed)

        logger.debug("ShortcutManager initialized")

    @inject_self.property
    def definitions(self) -> dict[str, ActionDefinition]:
        """Get all registered action definitions."""
        return self._definitions

    @inject_self
    def register_definitions(self, definitions: Iterable[ActionDefinition]) -> None:
        """
        Register new action definitions (usually from plugins).

        This ensures that the actions are known and have default values in settings
        if not already customized by the user.

        Args:
            definitions: The action definitions to register.
        """
        existing_ids = {s.action_id for s in SettingsManager.global_settings.shortcuts}

        for definition in definitions:
            self._definitions[definition] = definition

            if definition not in existing_ids:
                SettingsManager.global_settings.shortcuts.append(
                    ShortcutConfig(action_id=definition, key_sequence=definition.default_key)
                )

    @inject_self
    def register_action(
        self,
        action_id: str,
        action: QAction,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> None:
        """
        Register a QAction for shortcut management.

        Args:
            action_id: The identifier for this shortcut.
            action: The QAction to manage.
            context: The context in which the shortcut should be active.
        """
        action.setShortcutContext(context)

        self._actions[action_id].add(action)
        self._update_action(action_id, action)

        logger.debug("Registered action for %s: %r", action_id, action.text())

    @inject_self
    def register_shortcut(
        self,
        action_id: str,
        callback: Callable[[], Any],
        parent: QWidget,
        *,
        context: Qt.ShortcutContext = Qt.ShortcutContext.WidgetWithChildrenShortcut,
    ) -> QShortcut:
        """
        Create and register a QShortcut for shortcut management.

        Args:
            action_id: The identifier for this shortcut.
            callback: The function to call when the shortcut is activated.
            parent: The parent widget that determines shortcut scope.
            context: The context in which the shortcut should be active.

        Returns:
            The created QShortcut instance.
        """
        shortcut = QShortcut(parent)
        shortcut.setContext(context)
        shortcut.activated.connect(callback)

        # Add ambiguity detection for runtime conflicts
        shortcut.activatedAmbiguously.connect(
            lambda: logger.warning(
                "Ambiguous shortcut '%s' triggered. Action: %s",
                shortcut.key().toString(),
                self._definitions[action_id].label if action_id in self._definitions else action_id,
            )
        )

        self._shortcuts[action_id].add(shortcut)
        self._update_shortcut(action_id, shortcut)

        logger.debug("Registered shortcut for %s in context %r", action_id, context.__class__.__name__)
        return shortcut

    @inject_self
    def unregister_shortcut(self, action_id: str, shortcut: QShortcut) -> None:
        """Unregister a previously registered shortcut."""
        if action_id in self._shortcuts:
            self._shortcuts[action_id].discard(shortcut)
            logger.debug("Unregistered shortcut for %s", action_id)
        else:
            logger.warning("Cannot unregister shortcut: action ID %r is not registered", action_id)

    @inject_self
    def get_key(self, action_id: str) -> str:
        """Get the current key sequence for an action from settings."""
        return SettingsManager.global_settings.get_key(action_id)

    @inject_self
    def get_hierarchy(self, action_id: str) -> Literal[0, 1, 2, 3]:
        """
        Retrieves the widest (highest value) shortcut context scope defined for the given action ID.

        Returns the maximum value based on `SCOPE_HIERARCHY` (i.e., the most global scope).
        """
        hierarchies = set[Literal[0, 1, 2, 3]]()

        if action_id in self._actions:
            hierarchies.update(self.SCOPE_HIERARCHY[action.shortcutContext()] for action in self._actions[action_id])

        if action_id in self._shortcuts:
            hierarchies.update(self.SCOPE_HIERARCHY[shortcut.context()] for shortcut in self._shortcuts[action_id])

        if hierarchies:
            return max(hierarchies)

        context, value = max(self.SCOPE_HIERARCHY.items(), key=operator.itemgetter(1))
        logger.info("Assuming '%s' context for '%s' until a shortcut is registered", context.name, action_id)

        return value

    def _update_action(self, action_id: str, action: QAction) -> None:
        key = self.get_key(action_id)
        action.setShortcut(key)

        if not key:
            return

        native = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)

        if (original := action.property("original_tooltip")) is None:
            original = action.toolTip()
            action.setProperty("original_tooltip", original)

        action.setToolTip(f"{original} ({native})" if original else f"({native})")

    def _update_shortcut(self, action_id: str, shortcut: QShortcut) -> None:
        shortcut.setKey(QKeySequence(self.get_key(action_id)))

    @Slot()
    def _on_settings_changed(self) -> None:
        logger.debug("Hot-reloading shortcuts...")

        for aid in self._definitions:
            for action in self._actions.get(aid, ()):
                self._update_action(aid, action)

            for shortcut in self._shortcuts.get(aid, ()):
                self._update_shortcut(aid, shortcut)

        logger.debug("Shortcuts hot-reloaded")
        # FIXME:
        # self._check_conflicts()

    @inject_self
    def _check_conflicts(self) -> None:
        # Unused
        # This method is too fragile because two shortcuts could work with the same key sequence
        # but with a difference parent context
        key_map = dict[str, list[str]]()

        for action_id in self._definitions:
            if not (key := self.get_key(action_id)):
                continue

            key_map.setdefault(key, []).append(action_id)

        for key, action_ids in key_map.items():
            if len(action_ids) > 1:
                labels = [self._definitions[aid].label if aid in self._definitions else aid for aid in action_ids]
                logger.warning(
                    "Shortcut conflict detected: key '%s' is assigned to multiple actions: %s",
                    key,
                    ", ".join(labels),
                )
