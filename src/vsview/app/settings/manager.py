"""Settings manager for vsview."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from inspect import Signature
from logging import DEBUG, getLogger
from typing import Any

from jetpytools import MISSING, CustomTypeError, Singleton, inject_self
from pydantic import ValidationError
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QApplication
from rich.pretty import pretty_repr

from ...env import getenv_bool
from ...types import VPath
from ..utils import WeakableSignals
from .models import BaseSettings, GlobalSettings, LocalSettings, validate_lenient

if sys.version_info >= (3, 13):
    from typing import TypeIs
else:
    from typing_extensions import TypeIs

logger = getLogger(__name__)


class SettingsSignals(WeakableSignals):
    """Qt signals for settings change notifications."""

    globalChanged = Signal()
    localChanged = Signal(str)  # Emits the local settings file path
    aboutToSaveGlobal = Signal()
    aboutToSaveLocal = Signal(str)  # Emits the local settings file path

    def connect_global_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.globalChanged, slot, type)

    def connect_local_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.localChanged, slot, type)

    def connect_about_global_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.aboutToSaveGlobal, slot, type)

    def connect_about_local_weak(
        self,
        slot: Callable[..., Any],
        type: Qt.ConnectionType = Qt.ConnectionType.AutoConnection,
    ) -> None:
        self._wrap_method_signal(self.aboutToSaveLocal, slot, type)


class SettingsManager(Singleton):
    """Manages loading and saving of global and local settings."""

    def __init__(self, noop: bool = False) -> None:
        self._global_settings = self.default_global_settings
        self._local_settings = dict[str, LocalSettings]()  # Keyed by path hash
        self._files = dict[VPath, _SettingsFile]()
        self._signals = SettingsSignals(QApplication.instance())

        self._noop = noop

        self._load_global()
        logger.debug("SettingsManager initialized")

    @inject_self.property
    def signals(self) -> SettingsSignals:
        """Access the Qt signals for settings changes."""
        return self._signals

    @inject_self.property
    def global_settings(self) -> GlobalSettings:
        """Get the current global settings."""
        return self._global_settings

    @inject_self.property
    def default_global_settings(self) -> GlobalSettings:
        """Build a new model with the default global settings on each access."""
        return GlobalSettings()

    @inject_self.property
    def default_local_settings(self) -> LocalSettings:
        """Build a new model with the default local settings on each access."""
        return LocalSettings()

    @inject_self
    def get_local_settings(self, script_path: os.PathLike[str]) -> LocalSettings:
        """
        Get local settings for a specific script.

        Args:
            script_path: Path to the script file.

        Returns:
            The local settings for the script, loaded from disk or defaults.
        """
        from ..utils import path_to_hash

        path_hash = path_to_hash(script_path)

        if path_hash not in self._local_settings:
            logger.debug("%s is not in loaded local settings (from %s)", path_hash, lambda: VPath(script_path).name)
            self._load_local(script_path)

        return self._local_settings.get(path_hash) or self.default_local_settings

    @inject_self
    def save_global(
        self,
        settings: GlobalSettings | Callable[[], GlobalSettings] | None = None,
        path: os.PathLike[str] | None = None,
        emit_changed: bool = True,
    ) -> bool:
        """
        Save global settings to disk.

        Args:
            settings: The global settings to save, or a callable returning them.
                A callable is called after `aboutToSaveGlobal`, so it sees the writes made by its slots.
            path: The file to write. Defaults to the global settings path.
            emit_changed: Emit `globalChanged` after a successful write. `aboutToSaveGlobal` is always emitted.

        Returns:
            False when the file could not be written, True otherwise.
        """
        try:
            self._signals.aboutToSaveGlobal.emit()
        except Exception:
            logger.exception("There was an error when emitting aboutToSaveGlobal")

        if callable(settings):
            settings = settings()

        self._global_settings = settings if settings is not None else self._global_settings
        path = VPath(path or GlobalSettings.path_env)

        try:
            if not self._noop:
                self._global_settings = self._get_file(path).write(self._global_settings)
                logger.debug("Saved global settings to: %s", path)
            if emit_changed:
                self._signals.globalChanged.emit()
        except Exception:
            logger.exception("Failed to save global settings")
            return False

        return True

    @inject_self
    def save_local(
        self,
        script_path: os.PathLike[str],
        settings: LocalSettings | Callable[[LocalSettings], LocalSettings] | Callable[[], LocalSettings] | None = None,
    ) -> bool:
        """
        Save local settings for a script to disk.

        Args:
            script_path: Path to the script file.
            settings: The local settings to save, or a callable returning them.

        Returns:
            False when the file could not be written, True otherwise.
        """
        from ..utils import path_to_hash

        script_path = VPath(script_path)
        settings_path = self.local_settings_path(script_path)

        try:
            self._signals.aboutToSaveLocal.emit(str(settings_path))
        except Exception:
            logger.exception("There was an error when emitting aboutToSaveLocal")

        current_settings = self.get_local_settings(script_path)

        if callable(settings):
            if _callable_one_param(settings):
                resolved_settings = settings(current_settings)
            elif _callable_no_param(settings):
                resolved_settings = settings()
            else:
                raise CustomTypeError
        elif settings is not None:
            resolved_settings = settings
        else:
            resolved_settings = current_settings

        self._local_settings[path_to_hash(script_path)] = resolved_settings

        try:
            if not self._noop:
                self._local_settings[path_to_hash(script_path)] = self._get_file(settings_path).write(resolved_settings)
                logger.debug("Saved local settings for %s to: %s", script_path, settings_path)
            self._signals.localChanged.emit(str(settings_path))
        except Exception:
            logger.exception("Failed to save local settings for %s", script_path)
            return False

        return True

    @staticmethod
    def local_settings_path(script_path: os.PathLike[str]) -> VPath:
        """
        Get the file path for a script's local settings.

        Args:
            script_path: Path to the script file.

        Returns:
            Path to the local settings JSON file.
        """
        from ..utils import path_to_hash

        return VPath(script_path).parent / ".vsjet" / "vsview" / f"{path_to_hash(script_path)}.json"

    def _load_global(self) -> None:
        if self._noop:
            logger.info("Loading with no config set")
            return

        # Always write the reference global settings file
        if not GlobalSettings.path.exists():
            with QSignalBlocker(self._signals):
                self.save_global(self.default_global_settings, GlobalSettings.path)

        file = self._get_file(GlobalSettings.path_env)
        raw = file.path.safe_read_bytes()

        if (settings := self._read_global(raw)) is not None:
            self._global_settings = settings
            # Merge in any new shortcuts that don't exist in the loaded settings
            self._merge_default_shortcuts()

        file.sync(raw, self._global_settings)

    def _read_global(self, raw: bytes | None) -> GlobalSettings | None:
        # path_env == path when the env var is unset
        path = GlobalSettings.path_env

        if raw is None and getenv_bool("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"):
            # The env-scoped file doesn't exist. Fall back to the reference file only if COPY is enabled
            path = GlobalSettings.path
            raw = path.safe_read_bytes()

        if raw is None:
            logger.info("Global settings file does not exist. Using defaults.")
            return None

        try:
            data = json.loads(raw.decode("utf-8"))
            settings, dropped = validate_lenient(GlobalSettings, data)
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
            logger.warning("Global settings file is malformed or unreadable. Using defaults.\nError: %s", e)
            logger.debug("Full traceback:", exc_info=True)
            path.save_as_backup()
            return None
        except Exception:
            logger.exception("Unhandled exception:")
            return None

        if dropped:
            logger.warning("Invalid global settings were reset to their defaults: %s", ", ".join(dropped))
            path.save_as_backup()

        logger.debug("Loaded global settings from %s", path)
        logger.log(DEBUG - 1, " %s", lambda: pretty_repr(settings))
        return settings

    def _merge_default_shortcuts(self) -> None:
        existing_action_ids = {s.action_id for s in self._global_settings.shortcuts}

        # Find shortcuts that exist in defaults but not in loaded settings
        missing_shortcuts = [
            shortcut
            for shortcut in self.default_global_settings.shortcuts
            if shortcut.action_id not in existing_action_ids
        ]

        if missing_shortcuts:
            logger.info(
                "Adding %d new shortcuts from defaults: %s",
                len(missing_shortcuts),
                [s.action_id for s in missing_shortcuts],
            )
            # Create new settings with merged shortcuts
            self._global_settings = self._global_settings.model_copy(
                update={"shortcuts": self._global_settings.shortcuts + missing_shortcuts}
            )

    def _load_local(self, script_path: os.PathLike[str]) -> None:
        from ..utils import path_to_hash

        script_path = VPath(script_path)
        settings_path = self.local_settings_path(script_path)

        if self._noop:
            logger.info("Loading with no config set")
            self._local_settings[path_to_hash(script_path)] = self.default_local_settings.model_copy(
                update={"source_path": script_path.to_str()}
            )
            return

        file = self._get_file(settings_path)
        raw = settings_path.safe_read_bytes()
        settings = self._read_local(script_path, settings_path, raw)

        if not settings.source_path:
            settings.source_path = script_path.to_str()

        self._local_settings[path_to_hash(script_path)] = settings
        file.sync(raw, settings)

    def _read_local(self, script_path: VPath, settings_path: VPath, raw: bytes | None) -> LocalSettings:
        if raw is None:
            logger.debug("Local settings file does not exist for %s. Using defaults.", script_path)
            return self.default_local_settings.model_copy(update={"source_path": script_path.to_str()})

        try:
            data = json.loads(raw.decode("utf-8"))
            settings, dropped = validate_lenient(LocalSettings, data)
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
            logger.error(
                "Local settings file is malformed or unreadable for %s. Using defaults.\nFile: %s\nError: %s",
                script_path,
                settings_path.resolve(),
                e,
            )
            logger.debug("Full traceback:", exc_info=True)
            settings_path.save_as_backup()
            return self.default_local_settings.model_copy(update={"source_path": script_path.to_str()})

        if dropped:
            logger.warning(
                "Invalid local settings for %s were reset to their defaults: %s",
                script_path,
                ", ".join(dropped),
            )
            settings_path.save_as_backup()

        logger.debug("Loaded local settings for %s", script_path)
        logger.log(DEBUG - 1, "%s", lambda: pretty_repr(settings))
        return settings

    def _get_file(self, path: VPath) -> _SettingsFile:
        if (file := self._files.get(path)) is None:
            file = self._files[path] = _SettingsFile(path)

        return file


class _SettingsFile:
    def __init__(self, path: VPath) -> None:
        self.path = path
        self._raw: bytes | None = None
        self._base: str | None = None

    def sync(self, raw: bytes | None, settings: BaseSettings) -> None:
        self._raw = raw
        self._base = settings.model_dump_json()

    def write[S: BaseSettings](self, settings: S) -> S:
        settings = self._merge_external_changes(settings)
        text = settings.model_dump_json(indent=2)
        raw = text.encode("utf-8")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.atomic_write(raw)

        self._raw, self._base = raw, text
        return settings

    def _merge_external_changes[S: BaseSettings](self, settings: S) -> S:
        theirs_raw = self.path.safe_read_bytes()

        if self._base is None or theirs_raw is None or theirs_raw == self._raw:
            return settings

        try:
            ours = json.loads(settings.model_dump_json())
            theirs = json.loads(theirs_raw.decode("utf-8"))
            merged, _ = validate_lenient(type(settings), _merge_changes(json.loads(self._base), ours, theirs))
        except (ValueError, TypeError):
            logger.warning(
                "Couldn't merge the settings saved by another vsview window into %s",
                self.path,
                exc_info=True,
            )
            return settings

        logger.info("Merged the settings saved by another vsview window into %s", self.path)
        return merged


def _merge_changes(base: Any, ours: Any, theirs: Any) -> Any:
    if not (isinstance(ours, dict) and isinstance(theirs, dict)):
        return ours if ours != base else theirs

    base = base if isinstance(base, dict) else {}
    merged = {key: theirs[key] for key in theirs if key not in ours}

    for key, value in ours.items():
        merged[key] = _merge_changes(base.get(key, MISSING), value, theirs[key]) if key in theirs else value

    return merged


def _callable_one_param(obj: object) -> TypeIs[Callable[[LocalSettings], LocalSettings]]:
    return callable(obj) and len(Signature.from_callable(obj).parameters) == 1


def _callable_no_param(obj: object) -> TypeIs[Callable[[], LocalSettings]]:
    return callable(obj) and len(Signature.from_callable(obj).parameters) == 0
