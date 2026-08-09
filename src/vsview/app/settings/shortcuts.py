"""Shortcut manager for hot-reloadable keyboard shortcuts."""

import ctypes
import ctypes.util
import operator
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from functools import wraps
from logging import getLogger
from typing import Any, Final, Literal, cast

from jetpytools import CustomNotImplementedError, Singleton, inject_self
from PySide6.QtCore import QKeyCombination, Qt, Slot
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
    def get_key(self, action_id: str) -> QKeySequence:
        """Get the current key sequence for an action from settings, applying layout mapping if default."""
        if (config := SettingsManager.global_settings.get_shortcut_config(action_id)) is None:
            return QKeySequence()

        if config.is_custom or (definition := self._definitions.get(action_id)) is None:
            return config.key_sequence

        return KeyboardLayoutMapper.translate_qwerty_to_active(definition.default_key)

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

        if key.isEmpty():
            return

        native = key.toString(QKeySequence.SequenceFormat.NativeText)

        if (original := action.property("original_tooltip")) is None:
            original = action.toolTip()
            action.setProperty("original_tooltip", original)

        action.setToolTip(f"{original} ({native})" if original else f"({native})")

    def _update_shortcut(self, action_id: str, shortcut: QShortcut) -> None:
        shortcut.setKey(self.get_key(action_id))

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
            if (key := self.get_key(action_id)).isEmpty():
                continue

            key_map.setdefault(key.toString(), []).append(action_id)

        for key, action_ids in key_map.items():
            if len(action_ids) > 1:
                labels = [self._definitions[aid].label if aid in self._definitions else aid for aid in action_ids]
                logger.warning(
                    "Shortcut conflict detected: key '%s' is assigned to multiple actions: %s",
                    key,
                    ", ".join(labels),
                )


# QWERTY physical key hardware scan codes (US QWERTY standard)
QWERTY_SCAN_CODES: Final[Mapping[str, int]] = {
    # Number row
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "0": 0x0B,
    "-": 0x0C,
    "=": 0x0D,
    # QWERTY row
    "Q": 0x10,
    "W": 0x11,
    "E": 0x12,
    "R": 0x13,
    "T": 0x14,
    "Y": 0x15,
    "U": 0x16,
    "I": 0x17,
    "O": 0x18,
    "P": 0x19,
    "[": 0x1A,
    "]": 0x1B,
    "\\": 0x2B,
    # ASDF row
    "A": 0x1E,
    "S": 0x1F,
    "D": 0x20,
    "F": 0x21,
    "G": 0x22,
    "H": 0x23,
    "J": 0x24,
    "K": 0x25,
    "L": 0x26,
    ";": 0x27,
    "'": 0x28,
    # ZXCV row
    "Z": 0x2C,
    "X": 0x2D,
    "C": 0x2E,
    "V": 0x2F,
    "B": 0x30,
    "N": 0x31,
    "M": 0x32,
    "/": 0x35,
    "`": 0x29,
    # Shifted symbol aliases
    "!": 0x02,
    "@": 0x03,
    "#": 0x04,
    "$": 0x05,
    "%": 0x06,
    "^": 0x07,
    "&": 0x08,
    "*": 0x09,
    "(": 0x0A,
    ")": 0x0B,
    "_": 0x0C,
    "+": 0x0D,
    "{": 0x1A,
    "}": 0x1B,
    "|": 0x2B,
    ":": 0x27,
    '"': 0x28,
    "<": 0x33,
    ">": 0x34,
    "?": 0x35,
    "~": 0x29,
}

# macOS virtual keycodes mapping for standard QWERTY layout
MACOS_KEYCODES: Final[Mapping[str, int]] = {
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    "0": 29,
    "-": 27,
    "=": 24,
    "Q": 12,
    "W": 13,
    "E": 14,
    "R": 15,
    "T": 17,
    "Y": 16,
    "U": 32,
    "I": 34,
    "O": 31,
    "P": 35,
    "[": 33,
    "]": 30,
    "\\": 42,
    "A": 0,
    "S": 1,
    "D": 2,
    "F": 3,
    "G": 5,
    "H": 4,
    "J": 38,
    "K": 40,
    "L": 37,
    ";": 41,
    "'": 39,
    "Z": 6,
    "X": 7,
    "C": 8,
    "V": 9,
    "B": 11,
    "N": 45,
    "M": 46,
    ",": 43,
    ".": 47,
    "/": 44,
    "`": 50,
    # Shifted symbol aliases
    "!": 18,
    "@": 19,
    "#": 20,
    "$": 21,
    "%": 23,
    "^": 22,
    "&": 26,
    "*": 28,
    "(": 25,
    ")": 29,
    "_": 27,
    "+": 24,
    "{": 33,
    "}": 30,
    "|": 42,
    ":": 41,
    '"': 39,
    "<": 43,
    ">": 47,
    "?": 44,
    "~": 50,
}


def fallback_logged[T: KeyboardLayoutMapper, R](func: Callable[[T, str], R]) -> Callable[[T, str], R | None]:
    @wraps(func)
    def wrapper(self: T, upper_base: str) -> R | None:
        try:
            return func(self, upper_base)
        except Exception as e:
            logger.warning("Layout translation failed for %s: %s", func, e)
            logger.debug("Full traceback:", exc_info=e)
            return None

    return wrapper


class KeyboardLayoutMapper(Singleton):
    def __init__(self) -> None:
        self._cache = dict[tuple[str, QKeySequence], QKeySequence]()

    @inject_self
    def translate_qwerty_to_active(self, key_sequence: QKeySequence) -> QKeySequence:
        if key_sequence.isEmpty():
            return QKeySequence()

        if (cache_key := (sys.platform, key_sequence)) in self._cache:
            return self._cache[cache_key]

        if key_sequence.count() != 1:
            logger.warning("Key sequence unsupported %s", key_sequence)
            return QKeySequence()

        comb = cast(QKeyCombination, key_sequence[0])  # type: ignore[index]
        modifiers = comb.keyboardModifiers()
        upper_base = QKeySequence(comb.key()).toString(QKeySequence.SequenceFormat.PortableText).upper()

        if upper_base not in QWERTY_SCAN_CODES:
            self._cache[cache_key] = key_sequence
            return key_sequence

        if sys.platform == "win32":
            translated_char = self._translate_win32(upper_base)
        elif sys.platform == "darwin":
            translated_char = self._translate_darwin(upper_base)
        elif sys.platform.startswith("linux"):
            translated_char = self._translate_linux(upper_base)
        else:
            raise CustomNotImplementedError

        if not translated_char:
            self._cache[cache_key] = key_sequence
            return key_sequence

        if translated_char.isalpha():
            translated_char = translated_char.upper()

        new_key = cast(QKeyCombination, QKeySequence(translated_char)[0]).key()  # type: ignore[index]
        self._cache[cache_key] = result_seq = QKeySequence(QKeyCombination(modifiers, new_key))
        return result_seq

    @fallback_logged
    def _translate_win32(self, upper_base: str) -> str | None:
        if (windll := getattr(ctypes, "windll", None)) is None:
            return None
        u32 = windll.user32
        u32.GetKeyboardLayout.restype = ctypes.c_void_p
        u32.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        u32.MapVirtualKeyExW.restype = ctypes.c_uint
        u32.ToUnicodeEx.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        u32.ToUnicodeEx.restype = ctypes.c_int

        hkl = u32.GetKeyboardLayout(0)
        sc = QWERTY_SCAN_CODES[upper_base]
        vk = u32.MapVirtualKeyExW(sc, 1, hkl)  # MAPVK_VSC_TO_VK
        if vk > 0:
            key_state = (ctypes.c_ubyte * 256)()
            buf = ctypes.create_unicode_buffer(5)
            ret = u32.ToUnicodeEx(vk, sc, key_state, buf, 5, 0, hkl)
            if ret > 0 and buf.value:
                return buf.value
        return None

    @fallback_logged
    def _translate_darwin(self, upper_base: str) -> str | None:
        if upper_base not in MACOS_KEYCODES or not (carbon_path := ctypes.util.find_library("Carbon")):
            return None

        carbon = ctypes.cdll.LoadLibrary(carbon_path)
        mac_keycode = MACOS_KEYCODES[upper_base]

        # Get current keyboard input source layout data pointer
        tis_source = carbon.TISCopyCurrentKeyboardInputSource()
        if not tis_source:
            return None

        # kTISPropertyUnicodeKeyLayoutData = "TISPropertyUnicodeKeyLayoutData"
        layout_data_ptr = carbon.TISGetInputSourceProperty(
            tis_source, ctypes.c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")
        )
        if not layout_data_ptr:
            return None

        buf = ctypes.create_unicode_buffer(4)
        length = ctypes.c_uint32(0)
        dead_key_state = ctypes.c_uint32(0)

        # UCKeyTranslate(layout_data, keycode, action, modifiers, keyboard_type, options, state, max_len, len, buf)
        res = carbon.UCKeyTranslate(
            layout_data_ptr,
            ctypes.c_uint16(mac_keycode),
            ctypes.c_uint16(0),  # kUCKeyActionDisplay
            ctypes.c_uint32(0),  # no modifiers
            ctypes.c_uint32(0),  # LMGetKbdType()
            ctypes.c_uint32(0),  # kUCKeyTranslateNoDeadKeysBit
            ctypes.byref(dead_key_state),
            ctypes.c_uint32(4),
            ctypes.byref(length),
            buf,
        )
        if res == 0 and length.value > 0 and buf.value:
            return buf.value

        return None

    @fallback_logged
    def _translate_linux(self, upper_base: str) -> str | None:
        xkb_path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
        libxkb = ctypes.cdll.LoadLibrary(xkb_path)

        # Define function return types
        libxkb.xkb_context_new.restype = ctypes.c_void_p
        libxkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        libxkb.xkb_state_new.restype = ctypes.c_void_p
        libxkb.xkb_state_key_get_utf8.restype = ctypes.c_int

        ctx = libxkb.xkb_context_new(0)
        if not ctx:
            return None

        keymap = libxkb.xkb_keymap_new_from_names(ctx, None, 0)
        if not keymap:
            libxkb.xkb_context_unref(ctx)
            return None

        state = libxkb.xkb_state_new(keymap)
        if not state:
            libxkb.xkb_keymap_unref(keymap)
            libxkb.xkb_context_unref(ctx)
            return None

        # EVDEV keycode = QWERTY hardware scancode + 8
        evdev_keycode = QWERTY_SCAN_CODES[upper_base] + 8
        buf = ctypes.create_string_buffer(8)
        ret = libxkb.xkb_state_key_get_utf8(state, ctypes.c_uint32(evdev_keycode), buf, 8)

        libxkb.xkb_state_unref(state)
        libxkb.xkb_keymap_unref(keymap)
        libxkb.xkb_context_unref(ctx)

        if ret > 0 and buf.value:
            return buf.value.decode("utf-8")

        return None
