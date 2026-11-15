from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from keyring.backends.fail import Keyring as FailKeyring
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.dialog import SettingsDialog
from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.metadata import Dropdown, LineEdit, SecretLineEdit
from vsview.app.settings.models import SettingEntry, ShortcutConfig
from vsview.app.settings.secrets import SecretsManager

pytestmark = [pytest.mark.unit, pytest.mark.qt]

PLUGIN_ID = "test_plugin"
PLUGIN_SECTION = "Plugin - Test Plugin"

type RegisterEntries = Callable[[Sequence[SettingEntry], Sequence[SettingEntry]], None]


@pytest.fixture
def register_entries(init_plugin_manager: PluginManager, monkeypatch: pytest.MonkeyPatch) -> RegisterEntries:
    def register(global_entries: Sequence[SettingEntry], local_entries: Sequence[SettingEntry] = ()) -> None:
        monkeypatch.setattr(PluginManager, "plugin_global_settings", list(global_entries))
        monkeypatch.setattr(PluginManager, "plugin_local_settings", list(local_entries))

    return register


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "script.vpy"
    path.touch()
    return path


def _entry(field: str, metadata: Any) -> SettingEntry:
    return SettingEntry(f"plugins.{PLUGIN_ID}.{field}", PLUGIN_SECTION, metadata)


def _open_dialog(qtbot: QtBot, script_path: Path | None = None) -> SettingsDialog:
    dialog = SettingsDialog(script_path)
    qtbot.addWidget(dialog)
    return dialog


def _find_by_tooltip[W: QWidget](dialog: SettingsDialog, widget_type: type[W], tooltip: str) -> W:
    return next(w for w in dialog.findChildren(widget_type) if w.toolTip() == tooltip)


def _click_apply(dialog: SettingsDialog) -> None:
    button = dialog.button_box.button(QDialogButtonBox.StandardButton.Apply)
    assert button is not None
    button.click()


def _use_backend(monkeypatch: pytest.MonkeyPatch, backend: object) -> None:
    monkeypatch.setattr(SecretsManager, "_keyring", property(lambda self: backend))


GLOBAL_OPTION = _entry("option", LineEdit(label="Option", tooltip="global-option"))
GLOBAL_SECRET = _entry(
    "token", SecretLineEdit(label="Token", namespace=PLUGIN_ID, context="auth", key="token", tooltip="global-secret")
)
LOCAL_COUNT = _entry("count", LineEdit(label="Count", tooltip="local-count", from_ui=int))


def test_apply_does_not_save_global_when_local_part_fails(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path
) -> None:
    register_entries([GLOBAL_OPTION], [LOCAL_COUNT])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original"}
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("not a number")

    _click_apply(dialog)

    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "original"}


def test_apply_does_not_store_secrets_when_local_part_fails(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path
) -> None:
    register_entries([GLOBAL_SECRET], [LOCAL_COUNT])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"token": None}
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "global-secret").setText("new_secret")
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("not a number")

    _click_apply(dialog)

    assert SecretsManager.get(PLUGIN_ID, "auth", "token") is None


def test_apply_saves_settings_and_secrets(qtbot: QtBot, register_entries: RegisterEntries, script: Path) -> None:
    register_entries([GLOBAL_OPTION, GLOBAL_SECRET], [LOCAL_COUNT])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original", "token": None}
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "global-secret").setText("new_secret")
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("7")

    _click_apply(dialog)

    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "changed", "token": None}
    assert SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] == {"count": 7}
    assert SecretsManager.get(PLUGIN_ID, "auth", "token") == "new_secret"


def test_apply_saves_other_settings_when_keyring_write_fails(
    qtbot: QtBot, register_entries: RegisterEntries, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_entries([GLOBAL_SECRET, GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original", "token": None}
    dialog = _open_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "global-secret").setText("new_secret")
    _use_backend(monkeypatch, FailKeyring())

    _click_apply(dialog)

    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "changed", "token": None}


BROKEN_DROPDOWN = Dropdown(label="Broken", items=lambda value: [("A", value)])


def test_failed_widget_creation_does_not_break_dialog(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path
) -> None:
    register_entries(
        [_entry("broken", BROKEN_DROPDOWN), GLOBAL_OPTION], [_entry("broken", BROKEN_DROPDOWN), LOCAL_COUNT]
    )
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original", "broken": "kept"}
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1, "broken": "kept"}
    dialog = _open_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("7")

    _click_apply(dialog)

    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "changed", "broken": "kept"}
    assert SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] == {"count": 7, "broken": "kept"}


def test_apply_keeps_shortcuts_of_unregistered_actions(qtbot: QtBot, register_entries: RegisterEntries) -> None:
    register_entries([], [])
    stored = ShortcutConfig(
        action_id="missing_plugin.action", key_sequence=QKeySequence("Ctrl+Shift+F9"), is_custom=True
    )
    SettingsManager.global_settings.shortcuts.append(stored)
    dialog = _open_dialog(qtbot)

    _click_apply(dialog)

    kept = SettingsManager.global_settings.get_shortcut_config("missing_plugin.action")
    assert kept is not None
    assert kept.key_sequence.toString(QKeySequence.SequenceFormat.PortableText) == "Ctrl+Shift+F9"
    assert kept.is_custom is True


def test_local_dependent_dropdown_is_populated_from_local_settings(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path
) -> None:
    mode = Dropdown(label="Mode", items=[("A", "a"), ("B", "b")], tooltip="local-mode")
    choice = Dropdown(
        label="Choice",
        items=lambda parent: [(f"{parent}-1", f"{parent}-1"), (f"{parent}-2", f"{parent}-2")],
        depends_on=f"plugins.{PLUGIN_ID}.mode",
        tooltip="local-choice",
    )
    register_entries([], [_entry("mode", mode), _entry("choice", choice)])
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"mode": "b", "choice": "b-2"}

    dialog = _open_dialog(qtbot, script)

    combo = _find_by_tooltip(dialog, QComboBox, "local-choice")
    assert [combo.itemData(i) for i in range(combo.count())] == ["b-1", "b-2"]
    assert combo.currentData() == "b-2"
