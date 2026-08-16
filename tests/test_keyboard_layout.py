from __future__ import annotations

import ctypes
import ctypes.util
import sys
from collections.abc import Generator
from typing import Any, Protocol, runtime_checkable
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from vsview.app.settings.action import ActionID
from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.models import ShortcutConfig
from vsview.app.settings.shortcuts import MACOS_KEYCODES, QWERTY_SCAN_CODES, KeyboardLayoutMapper, ShortcutManager

# ruff: noqa: N806


@runtime_checkable
class HasValue(Protocol):
    value: int


@pytest.fixture(autouse=True)
def clear_mapper_cache() -> Generator[None, None, None]:
    KeyboardLayoutMapper()._cache.clear()
    yield
    KeyboardLayoutMapper()._cache.clear()


def test_layout_mapper_native_translation() -> None:
    assert KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence("")) == QKeySequence()
    assert KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence("Space")) == QKeySequence("Space")
    assert KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence("F5")) == QKeySequence("F5")

    translated_ctrl_o = KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence("Ctrl+O"))
    assert not translated_ctrl_o.isEmpty()


def test_layout_mapper_shifted_symbols() -> None:
    symbols = ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "_", "+", "{", "}", "|", ":", '"', "<", ">", "?", "~"]
    for sym in symbols:
        res = KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence(f"Ctrl+{sym}"))
        assert not res.isEmpty()


def test_layout_mapper_multi_key_sequence() -> None:
    chord = QKeySequence("Ctrl+K, Ctrl+C")
    assert chord.count() > 1
    assert KeyboardLayoutMapper.translate_qwerty_to_active(chord) == QKeySequence()


def test_layout_mapper_non_qwerty_keys() -> None:
    keys = ["F1", "Escape", "Tab", "Return", "Delete", "Home", "End", "PageUp", "PageDown"]
    for key in keys:
        seq = QKeySequence(key)
        assert KeyboardLayoutMapper.translate_qwerty_to_active(seq) == seq


def test_win32_ctypes_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    # Win32 HKL (Keyboard Layout Handle)
    AZERTY_HKL = 0x040C
    # Win32 Virtual Key code 0x51 is VK_Q (ASCII 'Q'), corresponding to physical A key on AZERTY
    VK_Q = 0x51
    # MAPVK_VSC_TO_VK = 1 (translates hardware scancode to virtual key code in MapVirtualKeyExW)
    MAPVK_VSC_TO_VK = 1

    mock_user32 = MagicMock()
    mock_user32.GetKeyboardLayout.return_value = AZERTY_HKL

    def mock_map_vk(sc: int, map_type: int, hkl: Any) -> int:
        sc_val = sc.value if isinstance(sc, HasValue) else sc
        if sc_val == QWERTY_SCAN_CODES["A"]:
            return VK_Q
        return 0

    def mock_to_unicode(vk: int, sc: int, key_state: Any, buf: Any, buf_size: int, flags: int, hkl: Any) -> int:
        vk_val = vk.value if isinstance(vk, HasValue) else vk
        sc_val = sc.value if isinstance(sc, HasValue) else sc
        if vk_val == VK_Q and sc_val == QWERTY_SCAN_CODES["A"]:
            buf.value = "q"
            return 1  # 1 character translated and written to unicode buffer
        return 0

    mock_user32.MapVirtualKeyExW.side_effect = mock_map_vk
    mock_user32.ToUnicodeEx.side_effect = mock_to_unicode

    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    translated = KeyboardLayoutMapper.translate_qwerty_to_active(QKeySequence("Ctrl+A"))
    assert translated == QKeySequence("Ctrl+Q")
    mock_user32.GetKeyboardLayout.assert_called_once_with(0)
    mock_user32.MapVirtualKeyExW.assert_called_once_with(QWERTY_SCAN_CODES["A"], MAPVK_VSC_TO_VK, AZERTY_HKL)


def test_win32_ctypes_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    AZERTY_HKL = 0x040C
    VK_Q = 0x51

    mock_user32 = MagicMock()
    mock_user32.GetKeyboardLayout.return_value = AZERTY_HKL
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    mapper = KeyboardLayoutMapper()

    # MapVirtualKeyExW returns 0 (unmapped key)
    mock_user32.MapVirtualKeyExW.return_value = 0
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mapper._cache.clear()

    # ToUnicodeEx returns 0 (no character mapped)
    mock_user32.MapVirtualKeyExW.return_value = VK_Q
    mock_user32.ToUnicodeEx.return_value = 0
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mapper._cache.clear()

    # Exception during win32 ctypes execution (caught by @fallback_logged)
    mock_user32.ToUnicodeEx.side_effect = OSError("Win32 ctypes exception")
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")


def test_darwin_ctypes_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        ctypes.util,
        "find_library",
        lambda lib: "/System/Library/Frameworks/Carbon.framework/Carbon" if lib == "Carbon" else None,
    )

    # Mock pointer address (0x12345) for Carbon C global kTISPropertyUnicodeKeyLayoutData
    MOCK_LAYOUT_DATA_PTR = 0x12345
    # Dummy non-zero pointer handles for input source (0x9999) and property data (0x8888)
    MOCK_TIS_SOURCE = 0x9999
    MOCK_PROP_DATA = 0x8888

    mock_carbon = MagicMock()
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda path: mock_carbon if path == "/System/Library/Frameworks/Carbon.framework/Carbon" else MagicMock(),
    )
    monkeypatch.setattr(ctypes.c_void_p, "in_dll", lambda lib, name: ctypes.c_void_p(MOCK_LAYOUT_DATA_PTR))

    mock_carbon.TISCopyCurrentKeyboardInputSource.return_value = MOCK_TIS_SOURCE
    mock_carbon.TISGetInputSourceProperty.return_value = MOCK_PROP_DATA

    def mock_uc_key_translate(
        layout_data: Any,
        keycode: Any,
        action: Any,
        modifiers: Any,
        kbd_type: Any,
        options: Any,
        state_ptr: Any,
        max_len: Any,
        len_ptr: Any,
        buf: Any,
    ) -> int:
        kc = keycode.value if isinstance(keycode, HasValue) else keycode
        if kc == MACOS_KEYCODES["A"]:
            if hasattr(len_ptr, "_obj"):
                len_ptr._obj.value = 1  # 1 character translated
            buf.value = "q"
            return 0  # 0 indicates noErr status in macOS Carbon OSStatus
        return -1

    mock_carbon.UCKeyTranslate.side_effect = mock_uc_key_translate

    mapper = KeyboardLayoutMapper()
    translated = mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A"))
    assert translated == QKeySequence("Ctrl+Q")
    mock_carbon.TISCopyCurrentKeyboardInputSource.assert_called_once()
    mock_carbon.CFDataGetBytePtr.assert_called_once_with(MOCK_PROP_DATA)
    mock_carbon.CFRelease.assert_called_once_with(MOCK_TIS_SOURCE)

    # ctypes otherwise assumes C int returns/arguments, truncating opaque pointers on 64-bit platforms.
    assert mock_carbon.TISCopyCurrentKeyboardInputSource.restype == ctypes.c_void_p
    assert mock_carbon.TISGetInputSourceProperty.argtypes == [ctypes.c_void_p, ctypes.c_void_p]
    assert mock_carbon.TISGetInputSourceProperty.restype == ctypes.c_void_p
    assert mock_carbon.UCKeyTranslate.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    assert mock_carbon.UCKeyTranslate.restype == ctypes.c_int32
    assert mock_carbon.CFDataGetBytePtr.argtypes == [ctypes.c_void_p]
    assert mock_carbon.CFDataGetBytePtr.restype == ctypes.c_void_p
    assert mock_carbon.CFRelease.argtypes == [ctypes.c_void_p]
    assert mock_carbon.CFRelease.restype is None


def test_darwin_ctypes_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    MOCK_LAYOUT_DATA_PTR = 0x12345
    MOCK_TIS_SOURCE = 0x9999
    # Carbon status code -50 represents paramErr (invalid argument error)
    PARAM_ERR_STATUS = -50

    mapper = KeyboardLayoutMapper()

    # Carbon library not found
    monkeypatch.setattr(ctypes.util, "find_library", lambda lib: None)
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mapper._cache.clear()

    # TISCopyCurrentKeyboardInputSource returns None/0
    monkeypatch.setattr(ctypes.util, "find_library", lambda lib: "/path/to/Carbon")
    mock_carbon = MagicMock()
    mock_carbon.TISCopyCurrentKeyboardInputSource.return_value = 0
    monkeypatch.setattr(ctypes, "CDLL", lambda path: mock_carbon)

    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mapper._cache.clear()

    # TISGetInputSourceProperty returns None/0
    mock_carbon.TISCopyCurrentKeyboardInputSource.return_value = MOCK_TIS_SOURCE
    monkeypatch.setattr(ctypes.c_void_p, "in_dll", lambda lib, name: None)
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mapper._cache.clear()

    # UCKeyTranslate returns non-zero error status
    monkeypatch.setattr(ctypes.c_void_p, "in_dll", lambda lib, name: ctypes.c_void_p(MOCK_LAYOUT_DATA_PTR))
    mock_carbon.UCKeyTranslate.return_value = PARAM_ERR_STATUS
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")


def test_linux_ctypes_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        ctypes.util, "find_library", lambda lib: "/usr/lib/libxkbcommon.so.0" if lib == "xkbcommon" else None
    )

    # Dummy pointer addresses for opaque xkb C context, keymap, and state handles
    MOCK_CTX_PTR = 100
    MOCK_KEYMAP_PTR = 200
    MOCK_STATE_PTR = 300
    # Linux kernel EVDEV keycode offset applied to hardware scan code (evdev_keycode = scan_code + 8)
    EVDEV_OFFSET = 8

    mock_libxkb = MagicMock()
    monkeypatch.setattr(ctypes, "CDLL", lambda path: mock_libxkb)

    mock_libxkb.xkb_context_new.return_value = MOCK_CTX_PTR
    mock_libxkb.xkb_keymap_new_from_names.return_value = MOCK_KEYMAP_PTR
    mock_libxkb.xkb_state_new.return_value = MOCK_STATE_PTR

    def mock_get_utf8(state: Any, keycode: Any, buf: Any, size: int) -> int:
        kc = keycode.value if isinstance(keycode, HasValue) else keycode
        expected_evdev = QWERTY_SCAN_CODES["A"] + EVDEV_OFFSET
        if kc == expected_evdev:
            buf.value = b"q"
            return 1  # 1 byte written to string buffer
        return 0

    mock_libxkb.xkb_state_key_get_utf8.side_effect = mock_get_utf8

    mapper = KeyboardLayoutMapper()
    translated = mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A"))
    assert translated == QKeySequence("Ctrl+Q")

    mock_libxkb.xkb_context_new.assert_called_once_with(0)  # XKB_CONTEXT_NO_FLAGS = 0
    mock_libxkb.xkb_keymap_new_from_names.assert_called_once_with(
        MOCK_CTX_PTR, None, 0
    )  # XKB_KEYMAP_COMPILE_NO_FLAGS = 0
    mock_libxkb.xkb_state_new.assert_called_once_with(MOCK_KEYMAP_PTR)

    # ctypes otherwise assumes C int arguments, truncating opaque pointers on 64-bit platforms.
    assert mock_libxkb.xkb_context_new.argtypes == [ctypes.c_int]
    assert mock_libxkb.xkb_context_unref.argtypes == [ctypes.c_void_p]
    assert mock_libxkb.xkb_keymap_new_from_names.argtypes == [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    assert mock_libxkb.xkb_keymap_unref.argtypes == [ctypes.c_void_p]
    assert mock_libxkb.xkb_state_new.argtypes == [ctypes.c_void_p]
    assert mock_libxkb.xkb_state_unref.argtypes == [ctypes.c_void_p]
    assert mock_libxkb.xkb_state_key_get_utf8.argtypes == [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
    ]

    # Verify C resource cleanup (unref calls)
    mock_libxkb.xkb_state_unref.assert_called_once_with(MOCK_STATE_PTR)
    mock_libxkb.xkb_keymap_unref.assert_called_once_with(MOCK_KEYMAP_PTR)
    mock_libxkb.xkb_context_unref.assert_called_once_with(MOCK_CTX_PTR)


def test_linux_ctypes_failures_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    mock_libxkb = MagicMock()
    monkeypatch.setattr(ctypes, "CDLL", lambda path: mock_libxkb)

    MOCK_CTX_PTR = 100
    MOCK_KEYMAP_PTR = 200

    mapper = KeyboardLayoutMapper()

    # xkb_context_new fails (returns 0)
    mock_libxkb.xkb_context_new.return_value = 0
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mock_libxkb.xkb_keymap_unref.assert_not_called()
    mock_libxkb.xkb_context_unref.assert_not_called()
    mapper._cache.clear()
    mock_libxkb.reset_mock()

    # xkb_keymap_new_from_names fails (returns 0) -> ctx must be unref'd
    mock_libxkb.xkb_context_new.return_value = MOCK_CTX_PTR
    mock_libxkb.xkb_keymap_new_from_names.return_value = 0
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mock_libxkb.xkb_context_unref.assert_called_once_with(MOCK_CTX_PTR)
    mock_libxkb.xkb_keymap_unref.assert_not_called()
    mock_libxkb.xkb_state_unref.assert_not_called()
    mapper._cache.clear()
    mock_libxkb.reset_mock()

    # xkb_state_new fails (returns 0) -> keymap and ctx must be unref'd
    mock_libxkb.xkb_context_new.return_value = MOCK_CTX_PTR
    mock_libxkb.xkb_keymap_new_from_names.return_value = MOCK_KEYMAP_PTR
    mock_libxkb.xkb_state_new.return_value = 0
    assert mapper.translate_qwerty_to_active(QKeySequence("Ctrl+A")) == QKeySequence("Ctrl+A")
    mock_libxkb.xkb_keymap_unref.assert_called_once_with(MOCK_KEYMAP_PTR)
    mock_libxkb.xkb_context_unref.assert_called_once_with(MOCK_CTX_PTR)
    mock_libxkb.xkb_state_unref.assert_not_called()


def test_shortcut_manager_get_key(qapp: QApplication) -> None:
    manager = ShortcutManager()

    manager.get_key(ActionID.RELOAD)
    custom_cfg = ShortcutConfig(action_id=ActionID.RELOAD, key_sequence=QKeySequence("Ctrl+Shift+R"), is_custom=True)

    for idx, s in enumerate(SettingsManager.global_settings.shortcuts):
        if s.action_id == ActionID.RELOAD:
            SettingsManager.global_settings.shortcuts[idx] = custom_cfg
            break

    assert manager.get_key(ActionID.RELOAD) == QKeySequence("Ctrl+Shift+R")
