from __future__ import annotations

import gc
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from jetpytools import CustomTypeError
from PySide6.QtGui import QKeySequence

from vsview.app.settings.manager import SettingsManager, SettingsSignals
from vsview.app.settings.models import GlobalSettings, LocalSettings, ShortcutConfig
from vsview.app.utils import path_to_hash

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class DummyReceiver:
    """Helper dummy receiver with bound method slots for weak reference testing."""

    def __init__(self) -> None:
        self.global_called = 0
        self.local_called = list[str]()

    def on_global_changed(self) -> None:
        self.global_called += 1

    def on_local_changed(self, path_hash: str) -> None:
        self.local_called.append(path_hash)


def test_settings_signals_weak_referencing() -> None:
    signals = SettingsSignals()
    receiver = DummyReceiver()

    signals.connect_global_weak(receiver.on_global_changed)
    signals.connect_local_weak(receiver.on_local_changed)

    # Verify emission invokes the callback
    signals.globalChanged.emit()
    assert receiver.global_called == 1

    signals.localChanged.emit("test_hash_1")
    assert receiver.local_called == ["test_hash_1"]

    # Also test aboutToSave weak connections
    about_receiver = DummyReceiver()
    signals.connect_about_global_weak(about_receiver.on_global_changed)
    signals.connect_about_local_weak(about_receiver.on_local_changed)

    signals.aboutToSaveGlobal.emit()
    assert about_receiver.global_called == 1
    signals.aboutToSaveLocal.emit("about_hash_1")
    assert about_receiver.local_called == ["about_hash_1"]

    # Delete receivers and trigger garbage collection
    del receiver
    del about_receiver
    gc.collect()

    # Verify subsequent emissions do NOT raise an exception and cleanly remove the dead weak reference
    signals.globalChanged.emit()
    signals.localChanged.emit("test_hash_2")
    signals.aboutToSaveGlobal.emit()
    signals.aboutToSaveLocal.emit("about_hash_2")

    # Second emissions to verify disconnected state
    signals.globalChanged.emit()
    signals.localChanged.emit("test_hash_3")


def test_settings_manager_load_default(fresh_settings_manager: Callable[[], SettingsManager]) -> None:
    # Ensure settings file does not exist initially
    if GlobalSettings.path.exists():
        GlobalSettings.path.unlink()
    assert GlobalSettings.path.exists() is False

    # Reset singleton instance to ensure a clean initial load
    sm = fresh_settings_manager()

    # Verify global_settings initializes with valid defaults
    assert isinstance(sm.global_settings, GlobalSettings)

    defaults = sm.default_global_settings
    assert sm.global_settings.timeline.mode == defaults.timeline.mode
    assert sm.global_settings.timeline.seek_step == defaults.timeline.seek_step
    assert sm.global_settings.playback.default_volume == defaults.playback.default_volume
    assert sm.global_settings.appearance.theme == defaults.appearance.theme
    assert sm.global_settings.status_message_timeout == defaults.status_message_timeout

    # Class-level property accessor via inject_self
    assert SettingsManager.global_settings.timeline.mode == defaults.timeline.mode

    # File should have been automatically written with reference default settings
    assert GlobalSettings.path.exists() is True


def test_settings_manager_corrupted_json_recovery(fresh_settings_manager: Callable[[], SettingsManager]) -> None:
    # Write invalid/malformed JSON (syntax error) to GlobalSettings.path
    GlobalSettings.path.parent.mkdir(parents=True, exist_ok=True)
    GlobalSettings.path.write_text("{ unclosed invalid json: [", encoding="utf-8")

    # Reinitialize singleton against corrupted file
    sm = fresh_settings_manager()

    # Verify it recovers gracefully with default_global_settings without crashing
    assert isinstance(sm.global_settings, GlobalSettings)
    assert sm.global_settings.timeline.mode == sm.default_global_settings.timeline.mode
    assert sm.global_settings.timeline.seek_step == sm.default_global_settings.timeline.seek_step
    assert sm.global_settings.playback.default_volume == sm.default_global_settings.playback.default_volume
    assert SettingsManager.global_settings.timeline.mode == sm.default_global_settings.timeline.mode


def test_settings_manager_invalid_schema_recovery(fresh_settings_manager: Callable[[], SettingsManager]) -> None:
    # Write JSON with invalid data types violating Pydantic schema to GlobalSettings.path
    malformed_schema_data = {
        "status_message_timeout": "not_an_integer_value",
        "timeline": {"seek_step": "invalid_seek_step"},
    }
    GlobalSettings.path.parent.mkdir(parents=True, exist_ok=True)
    GlobalSettings.path.write_text(json.dumps(malformed_schema_data), encoding="utf-8")

    # Reinitialize singleton against invalid schema file
    sm = fresh_settings_manager()

    # Verify graceful fallback to default_global_settings
    assert isinstance(sm.global_settings, GlobalSettings)
    assert sm.global_settings.status_message_timeout == sm.default_global_settings.status_message_timeout
    assert sm.global_settings.timeline.seek_step == sm.default_global_settings.timeline.seek_step
    assert sm.global_settings.playback.default_volume == sm.default_global_settings.playback.default_volume
    assert SettingsManager.global_settings.status_message_timeout == sm.default_global_settings.status_message_timeout


def test_settings_manager_save_and_signals() -> None:
    sm = SettingsManager()

    # Modify a setting on SettingsManager.global_settings
    sm.global_settings.timeline.seek_step = 60
    sm.global_settings.playback.default_volume = 0.85

    # Set up spies/receivers for aboutToSaveGlobal and globalChanged
    events = list[str]()

    def on_about_to_save() -> None:
        events.append("aboutToSaveGlobal")

    def on_global_changed() -> None:
        events.append("globalChanged")

    sm.signals.aboutToSaveGlobal.connect(on_about_to_save)
    sm.signals.globalChanged.connect(on_global_changed)

    # Call SettingsManager.save_global()
    SettingsManager.save_global()

    # Verify signals fired in the expected sequence
    assert events == ["aboutToSaveGlobal", "globalChanged"]

    # Verify the file is created on disk, contains valid JSON, and matches modified values
    assert GlobalSettings.path.exists() is True
    saved_data = json.loads(GlobalSettings.path.read_text(encoding="utf-8"))
    assert saved_data["timeline"]["seek_step"] == 60
    assert saved_data["playback"]["default_volume"] == 0.85


def test_settings_manager_local_settings(tmp_path: Path) -> None:
    sm = SettingsManager()

    script1 = tmp_path / "video_script_alpha.vpy"
    script2 = tmp_path / "video_script_beta.vpy"
    script1.touch()
    script2.touch()

    # Test SettingsManager.get_local_settings(script_path)
    settings1 = sm.get_local_settings(script1)
    assert isinstance(settings1, LocalSettings)
    assert settings1.last_frame == 0

    # Modify setting for script1
    settings1.last_frame = 350
    settings1.playback.current_volume = 0.3

    # Set up signal listeners
    about_local_emitted = list[str]()
    local_changed_emitted = list[str]()

    sm.signals.aboutToSaveLocal.connect(about_local_emitted.append)
    sm.signals.localChanged.connect(local_changed_emitted.append)

    # Save local settings
    SettingsManager.save_local(script1, settings1)

    expected_path1 = SettingsManager.local_settings_path(script1)
    hash1 = path_to_hash(script1)

    # Verify aboutToSaveLocal and localChanged signals fire with the correct script path hash
    assert len(about_local_emitted) == 1
    assert about_local_emitted[0] == str(expected_path1)
    assert hash1 in about_local_emitted[0]
    assert Path(about_local_emitted[0]).stem == hash1

    assert len(local_changed_emitted) == 1
    assert local_changed_emitted[0] == str(expected_path1)
    assert hash1 in local_changed_emitted[0]
    assert Path(local_changed_emitted[0]).stem == hash1

    # Verify script1 settings were written to disk
    assert expected_path1.exists() is True
    disk_data1 = json.loads(expected_path1.read_text(encoding="utf-8"))
    assert disk_data1["last_frame"] == 350
    assert disk_data1["playback"]["current_volume"] == 0.3

    # Verify local settings hashing isolates settings for different script paths
    expected_path2 = SettingsManager.local_settings_path(script2)
    hash2 = path_to_hash(script2)
    assert hash1 != hash2
    assert expected_path1 != expected_path2

    settings2 = sm.get_local_settings(script2)
    assert isinstance(settings2, LocalSettings)
    assert settings2.last_frame == 0
    assert expected_path2.exists() is False


def test_settings_manager_save_local_callables(tmp_path: Path) -> None:
    sm = SettingsManager()
    script = tmp_path / "script_callables.vpy"
    script.touch()

    # 1-parameter callable updater
    sm.save_local(script, lambda s: s.model_copy(update={"last_frame": 500}))
    assert sm.get_local_settings(script).last_frame == 500

    # 0-parameter callable
    sm.save_local(script, lambda: LocalSettings(last_frame=600))
    assert sm.get_local_settings(script).last_frame == 600

    # Invalid callable signature raises CustomTypeError
    with pytest.raises(CustomTypeError, match=r"^None$"):
        sm.save_local(script, lambda _a, _b: LocalSettings())  # type: ignore[arg-type]


def test_settings_manager_local_recovery(tmp_path: Path) -> None:
    sm = SettingsManager()

    # 1. Corrupted JSON recovery
    script_corrupt = tmp_path / "script_corrupt.vpy"
    script_corrupt.touch()
    loc_path_corrupt = SettingsManager.local_settings_path(script_corrupt)
    loc_path_corrupt.parent.mkdir(parents=True, exist_ok=True)
    loc_path_corrupt.write_text("{ corrupt json: [", encoding="utf-8")

    recovered1 = sm.get_local_settings(script_corrupt)
    assert recovered1.last_frame == 0
    assert recovered1.source_path == str(script_corrupt)

    # 2. Invalid schema recovery
    script_invalid = tmp_path / "script_invalid.vpy"
    script_invalid.touch()
    loc_path_invalid = SettingsManager.local_settings_path(script_invalid)
    loc_path_invalid.parent.mkdir(parents=True, exist_ok=True)
    loc_path_invalid.write_text(json.dumps({"last_frame": "not_an_int"}), encoding="utf-8")

    recovered2 = sm.get_local_settings(script_invalid)
    assert recovered2.last_frame == 0
    assert recovered2.source_path == str(script_invalid)


def test_settings_manager_shortcuts_merge(fresh_settings_manager: Callable[[], SettingsManager]) -> None:
    sm = SettingsManager()

    # Create config with only one shortcut (subset of defaults)
    partial_shortcut = ShortcutConfig(action_id="custom_action", key_sequence=QKeySequence("Ctrl+Shift+X"))
    custom_settings = sm.default_global_settings.model_copy(update={"shortcuts": [partial_shortcut]})

    GlobalSettings.path.parent.mkdir(parents=True, exist_ok=True)
    GlobalSettings.path.write_text(custom_settings.model_dump_json(indent=2), encoding="utf-8")

    # Reload global settings
    new_sm = fresh_settings_manager()

    action_ids = {s.action_id for s in new_sm.global_settings.shortcuts}
    assert "custom_action" in action_ids
    # Verify default shortcuts were also merged in
    default_ids = {s.action_id for s in new_sm.default_global_settings.shortcuts}
    assert default_ids.issubset(action_ids) is True


def test_settings_manager_noop_mode(
    tmp_path: Path,
    fresh_settings_manager: Callable[[], SettingsManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noop_sm = fresh_settings_manager()
    monkeypatch.setattr(noop_sm, "_noop", True)
    script = tmp_path / "script_noop.vpy"
    script.touch()

    loc_path = SettingsManager.local_settings_path(script)
    assert loc_path.exists() is False

    # Save local in noop mode
    noop_sm.save_local(script, LocalSettings(last_frame=999))
    # File should not be created on disk
    assert loc_path.exists() is False
    # But in-memory dictionary is updated
    assert noop_sm.get_local_settings(script).last_frame == 999
