"""Settings manager for vsview."""

import json
import weakref
from collections.abc import Callable
from inspect import ismethod
from logging import DEBUG, getLogger
from pathlib import Path
from typing import Any

from jetpytools import Singleton, inject_self
from pydantic import ValidationError
from PySide6.QtCore import QObject, QSignalBlocker, Signal, SignalInstance
from PySide6.QtWidgets import QApplication
from rich.pretty import pretty_repr

from ...env import getenv_bool
from .models import GlobalSettings, LocalSettings

logger = getLogger(__name__)


class SettingsSignals(QObject):
    """Qt signals for settings change notifications."""

    globalChanged = Signal()
    localChanged = Signal(str)  # Emits the script path hash
    aboutToSaveGlobal = Signal()
    aboutToSaveLocal = Signal(str)  # Emits the script path hash

    def connect_global_weak(self, slot: Callable[..., Any], *args: Any) -> None:
        self._wrap_global_method_signal(self.globalChanged, slot, *args)

    def connect_local_weak(self, slot: Callable[..., Any], *args: Any) -> None:
        self._wrap_local_method_signal(self.localChanged, slot, *args)

    def connect_about_global_weak(self, slot: Callable[..., Any], *args: Any) -> None:
        self._wrap_global_method_signal(self.aboutToSaveGlobal, slot, *args)

    def connect_about_local_weak(self, slot: Callable[..., Any], *args: Any) -> None:
        self._wrap_local_method_signal(self.aboutToSaveLocal, slot, *args)

    def _wrap_global_method_signal(self, signal: SignalInstance, slot: Callable[..., Any], *args: Any) -> None:
        weak = weakref.WeakMethod(slot) if ismethod(slot) else weakref.ref(slot)
        signal.connect(lambda: m() if (m := weak()) is not None else None, *args)

    def _wrap_local_method_signal(self, signal: SignalInstance, slot: Callable[..., Any], *args: Any) -> None:
        weak = weakref.WeakMethod(slot) if ismethod(slot) else weakref.ref(slot)
        signal.connect(lambda p: m(p) if (m := weak()) is not None else None, *args)


class SettingsManager(Singleton):
    """Manages loading and saving of global and local settings."""

    def __init__(self, noop: bool = False) -> None:
        self._global_settings = self.default_global_settings
        self._local_settings = dict[str, LocalSettings]()  # Keyed by path hash
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

    @inject_self.cached.property
    def default_global_settings(self) -> GlobalSettings:
        """Get the default global settings, lazily initialized."""

        return GlobalSettings()

    @inject_self.cached.property
    def default_local_settings(self) -> LocalSettings:
        """Get the default local settings, lazily initialized."""
        return LocalSettings()

    @inject_self.cached
    def get_local_settings(self, script_path: Path) -> LocalSettings:
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
            logger.debug("%s is not in loaded local settings (from %s)", path_hash, script_path)
            self._load_local(script_path)

        return self._local_settings.get(path_hash) or self.default_local_settings

    @inject_self.cached
    def save_global(self, settings: GlobalSettings | None = None, path: Path | None = None) -> None:
        """Save global settings to disk."""
        self._global_settings = settings if settings is not None else self._global_settings
        path = path or GlobalSettings.path_env

        try:
            self._signals.aboutToSaveGlobal.emit()
            if not self._noop:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self._global_settings.model_dump_json(indent=2), encoding="utf-8")
                logger.debug("Saved global settings to: %s", path)
            self._signals.globalChanged.emit()
        except Exception:
            logger.exception("Failed to save global settings")

    @inject_self.cached
    def save_local(self, script_path: Path, settings: LocalSettings) -> None:
        """
        Save local settings for a script to disk.

        Args:
            script_path: Path to the script file.
            settings: The local settings to save.
        """
        from ..utils import path_to_hash

        path_hash = path_to_hash(script_path)
        settings_path = self.local_settings_path(script_path)

        # Ensure source_path is set
        settings.source_path = str(script_path)
        self._local_settings[path_hash] = settings

        try:
            self._signals.aboutToSaveLocal.emit(str(settings_path))
            if not self._noop:
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
                logger.debug("Saved local settings for %s to: %s", script_path, settings_path)
            self._signals.localChanged.emit(str(settings_path))
        except Exception:
            logger.exception("Failed to save local settings for %s", script_path)

    @staticmethod
    def local_settings_path(script_path: Path) -> Path:
        """
        Get the file path for a script's local settings.

        Args:
            script_path: Path to the script file.

        Returns:
            Path to the local settings JSON file.
        """
        from ..utils import path_to_hash

        return script_path.parent / ".vsjet" / "vsview" / f"{path_to_hash(script_path)}.json"

    def _load_global(self) -> None:
        if self._noop:
            logger.info("Loading with no config set")
            return

        # Always write the reference global settings file
        if not GlobalSettings.path.exists():
            with QSignalBlocker(self._signals):
                self.save_global(self.default_global_settings, GlobalSettings.path)

        # Determine which file to load (path_env == path when the env var is unset)
        path = GlobalSettings.path_env

        if not path.exists():
            # Env-scoped file doesn't exist.
            # Fallback to the reference file only if COPY is enabled, otherwise keep defaults
            if not getenv_bool("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"):
                logger.info("Global settings file does not exist. Using defaults.")
                return

            path = GlobalSettings.path

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._global_settings = GlobalSettings.model_validate(data)

            # Merge in any new shortcuts that don't exist in the loaded settings
            self._merge_default_shortcuts()

            logger.debug("Loaded global settings from %s", path)
            logger.log(DEBUG - 1, " %s", lambda: pretty_repr(self._global_settings))
        except ValidationError as e:
            logger.warning("Global settings file is malformed. Using defaults.\nError: %s", e)
            logger.debug("Full traceback: %s", exc_info=True)
        except json.JSONDecodeError:
            logger.warning("Global settings file is empty or corrupted. Using defaults.")
            logger.debug("Full traceback: %s", exc_info=True)

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

    def _load_local(self, script_path: Path) -> None:
        from ..utils import path_to_hash

        path_hash = path_to_hash(script_path)
        settings_path = self.local_settings_path(script_path)

        fallback_settings = self.default_local_settings.model_copy(update={"source_path": str(script_path)})

        if self._noop:
            logger.info("Loading with no config set")
            self._local_settings[path_hash] = fallback_settings
            return

        if not settings_path.exists():
            logger.info("Local settings file does not exist for %s. Using defaults.", script_path.name)
            self._local_settings[path_hash] = fallback_settings
            return

        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))

            self._local_settings[path_hash] = LocalSettings.model_validate(data)

            logger.debug("Loaded local settings for %s", script_path)
            logger.log(DEBUG - 1, "%s", lambda: pretty_repr(self._local_settings[path_hash]))
        except ValidationError as e:
            logger.error(
                "Local settings file is malformed for %s. Using defaults.\nFile: %s\nError: %s",
                script_path,
                settings_path.resolve(),
                e,
            )
            self._local_settings[path_hash] = fallback_settings
        except json.JSONDecodeError:
            logger.exception("Failed to parse local settings JSON for %s", script_path)
            self._local_settings[path_hash] = fallback_settings
