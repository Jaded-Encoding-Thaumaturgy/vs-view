from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from keyring.backends.fail import Keyring as FailKeyring
from pydantic import BaseModel
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QLineEdit, QMessageBox, QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.app.plugins import specs
from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.dialog import SettingsDialog
from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.metadata import Dropdown, LineEdit, SecretLineEdit
from vsview.app.settings.models import GlobalSettings, SettingEntry, ShortcutConfig
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


@pytest.fixture(autouse=True)
def warnings_shown(mocker: MockerFixture) -> MagicMock:
    """Record the warning message boxes, which would otherwise block the test in a modal loop."""
    return mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok)


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


DEVICE = Dropdown(label="Device", items=[("A", "a"), ("B", "b")], tooltip="device")


@pytest.mark.parametrize("scope", ["global", "local"])
def test_apply_keeps_a_dropdown_value_that_is_no_longer_listed(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path, scope: str
) -> None:
    store = SettingsManager.global_settings if scope == "global" else SettingsManager.get_local_settings(script)
    register_entries(*(([_entry("device", DEVICE)], []) if scope == "global" else ([], [_entry("device", DEVICE)])))
    store.plugins[PLUGIN_ID] = {"device": "unplugged"}
    dialog = _open_dialog(qtbot, script)
    assert _find_by_tooltip(dialog, QComboBox, "device").currentIndex() == -1

    _click_apply(dialog)

    stored = SettingsManager.global_settings if scope == "global" else SettingsManager.get_local_settings(script)
    assert stored.plugins[PLUGIN_ID] == {"device": "unplugged"}


@pytest.mark.parametrize("scope", ["global", "local"])
def test_apply_stores_a_dropdown_value_chosen_after_an_unlisted_one(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path, scope: str
) -> None:
    store = SettingsManager.global_settings if scope == "global" else SettingsManager.get_local_settings(script)
    register_entries(*(([_entry("device", DEVICE)], []) if scope == "global" else ([], [_entry("device", DEVICE)])))
    store.plugins[PLUGIN_ID] = {"device": "unplugged"}
    dialog = _open_dialog(qtbot, script)
    _find_by_tooltip(dialog, QComboBox, "device").setCurrentIndex(1)

    _click_apply(dialog)

    stored = SettingsManager.global_settings if scope == "global" else SettingsManager.get_local_settings(script)
    assert stored.plugins[PLUGIN_ID] == {"device": "b"}


def test_apply_keeps_global_writes_made_in_about_to_save_global(
    qtbot: QtBot, register_entries: RegisterEntries
) -> None:
    register_entries([GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original"}

    def flush() -> None:
        SettingsManager.global_settings.plugins["flushing_plugin"] = {"state": "flushed"}

    SettingsManager.signals.aboutToSaveGlobal.connect(flush)
    dialog = _open_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")

    _click_apply(dialog)

    assert SettingsManager.global_settings.plugins["flushing_plugin"] == {"state": "flushed"}
    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "changed"}
    saved = json.loads(GlobalSettings.path_env.read_text(encoding="utf-8"))
    assert saved["plugins"]["flushing_plugin"] == {"state": "flushed"}
    assert saved["plugins"][PLUGIN_ID] == {"option": "changed"}


def _open_shown_dialog(qtbot: QtBot, script_path: Path | None = None) -> SettingsDialog:
    dialog = _open_dialog(qtbot, script_path)
    # The dialog deletes itself on close, which would leave qtbot nothing to tear down.
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


def _click_ok(dialog: SettingsDialog) -> None:
    button = dialog.button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert button is not None
    button.click()


def _shown_texts(warnings_shown: MagicMock) -> list[str]:
    return [call.args[2] for call in warnings_shown.call_args_list]


def _fail_replace_for(mocker: MockerFixture, target: Path) -> None:
    real_replace = os.replace

    def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if Path(dst).resolve() == target.resolve():
            raise OSError("disk full")
        real_replace(src, dst)

    mocker.patch.object(os, "replace", side_effect=replace)


def test_ok_keeps_the_dialog_open_and_shows_a_message_when_the_global_save_fails(
    qtbot: QtBot, register_entries: RegisterEntries, mocker: MockerFixture, warnings_shown: MagicMock
) -> None:
    register_entries([GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original"}
    dialog = _open_shown_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _fail_replace_for(mocker, GlobalSettings.path_env)

    _click_ok(dialog)

    assert dialog.isVisible() is True
    assert _shown_texts(warnings_shown) == [
        f"The global settings could not be written to:\n{GlobalSettings.path_env}\n\nSee the log for details."
    ]


def test_ok_keeps_the_dialog_open_and_shows_a_message_when_the_local_save_fails(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path, mocker: MockerFixture, warnings_shown: MagicMock
) -> None:
    register_entries([], [LOCAL_COUNT])
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_shown_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("7")
    local_path = SettingsManager.local_settings_path(script)
    _fail_replace_for(mocker, local_path)

    _click_ok(dialog)

    assert dialog.isVisible() is True
    assert _shown_texts(warnings_shown) == [
        f"The local settings could not be written to:\n{local_path}\n\nSee the log for details."
    ]


def test_ok_keeps_the_dialog_open_and_shows_a_message_when_a_value_cannot_be_read(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path, warnings_shown: MagicMock
) -> None:
    register_entries([], [LOCAL_COUNT])
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_shown_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("not a number")

    _click_ok(dialog)

    assert dialog.isVisible() is True
    assert _shown_texts(warnings_shown) == [
        (
            "The settings could not be applied:\n\n"
            f"{PLUGIN_SECTION} > Count: invalid literal for int() with base 10: 'not a number'"
        )
    ]
    assert SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] == {"count": 1}


class TypedPluginSettings(BaseModel):
    option: int = 0


class TypedPlugin:
    identifier = PLUGIN_ID
    display_name = "Test Plugin"
    global_settings_model = TypedPluginSettings
    local_settings_model = None

    @specs.hookimpl
    def vsview_register_tooldock(self) -> type[TypedPlugin]:
        return TypedPlugin


@pytest.fixture
def typed_plugin(init_plugin_manager: PluginManager) -> Generator[None]:
    plugin = TypedPlugin()
    init_plugin_manager.manager.register(plugin, name="typed_plugin")
    try:
        yield
    finally:
        init_plugin_manager.manager.unregister(plugin)


@pytest.mark.usefixtures("typed_plugin")
def test_apply_rejects_a_plugin_value_that_does_not_match_the_plugin_model(
    qtbot: QtBot,
    register_entries: RegisterEntries,
    warnings_shown: MagicMock,
) -> None:
    register_entries([GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = TypedPluginSettings(option=3)
    dialog = _open_shown_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("abc")

    _click_ok(dialog)

    assert dialog.isVisible() is True
    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == TypedPluginSettings(option=3)
    [text] = _shown_texts(warnings_shown)
    assert text.startswith("The settings could not be applied:\n\n1 validation error for TypedPluginSettings\noption\n")


@pytest.mark.usefixtures("typed_plugin")
def test_apply_stores_plugin_values_validated_by_the_plugin_model(
    qtbot: QtBot,
    register_entries: RegisterEntries,
    warnings_shown: MagicMock,
) -> None:
    register_entries([GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = TypedPluginSettings(option=3)
    dialog = _open_shown_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("12")

    _click_ok(dialog)

    assert dialog.isVisible() is False
    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == TypedPluginSettings(option=12)
    assert warnings_shown.call_count == 0


def test_ok_keeps_the_dialog_open_and_shows_a_message_when_a_secret_cannot_be_stored(
    qtbot: QtBot, register_entries: RegisterEntries, monkeypatch: pytest.MonkeyPatch, warnings_shown: MagicMock
) -> None:
    register_entries([GLOBAL_SECRET, GLOBAL_OPTION], [])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original", "token": None}
    dialog = _open_shown_dialog(qtbot)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "global-secret").setText("new_secret")
    _use_backend(monkeypatch, FailKeyring())

    _click_ok(dialog)

    assert dialog.isVisible() is True
    assert SettingsManager.global_settings.plugins[PLUGIN_ID] == {"option": "changed", "token": None}
    assert _shown_texts(warnings_shown) == [
        (
            f"These secrets could not be stored in the system keyring:\n{PLUGIN_SECTION} > Token\n\n"
            "The other settings were saved. See the log for details."
        )
    ]


def test_ok_closes_the_dialog_without_a_message_when_everything_is_saved(
    qtbot: QtBot, register_entries: RegisterEntries, script: Path, warnings_shown: MagicMock
) -> None:
    register_entries([GLOBAL_OPTION], [LOCAL_COUNT])
    SettingsManager.global_settings.plugins[PLUGIN_ID] = {"option": "original"}
    SettingsManager.get_local_settings(script).plugins[PLUGIN_ID] = {"count": 1}
    dialog = _open_shown_dialog(qtbot, script)
    _find_by_tooltip(dialog, QLineEdit, "global-option").setText("changed")
    _find_by_tooltip(dialog, QLineEdit, "local-count").setText("7")

    _click_ok(dialog)

    assert dialog.isVisible() is False
    assert warnings_shown.call_count == 0
