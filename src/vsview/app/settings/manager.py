"""Settings manager for vsview."""

import json
from logging import DEBUG, getLogger
from pathlib import Path

from jetpytools import Singleton, inject_self
from pydantic import ValidationError
from PySide6.QtCore import QObject, Signal
from rich.pretty import pretty_repr

from .models import DEFAULT_GLOBAL_SETTINGS, DEFAULT_LOCAL_SETTINGS, GLOBAL_SETTINGS_PATH, GlobalSettings, LocalSettings

logger = getLogger(__name__)


class SettingsSignals(QObject):
    """Qt signals for settings change notifications."""

    globalChanged = Signal()
    localChanged = Signal(str)  # Emits the script path hash


class SettingsManager(Singleton):
    """
    Manages loading and saving of global and local settings.

    Global settings are stored in the package directory.
    Local settings are stored per-script in .vsjet/vsview/{hash}.json.
    """

    def __init__(self) -> None:
        self._global_settings = DEFAULT_GLOBAL_SETTINGS
        self._local_settings = dict[str, LocalSettings]()  # Keyed by path hash
        self._signals = SettingsSignals()

        self._load_global()

        logger.debug("SettingsManager initialized. Global path: %s", GLOBAL_SETTINGS_PATH)

    @inject_self.property
    def signals(self) -> SettingsSignals:
        """Access the Qt signals for settings changes."""
        return self._signals

    @inject_self.property
    def global_settings(self) -> GlobalSettings:
        """Get the current global settings."""
        return self._global_settings

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
            self._load_local(script_path)

        return self._local_settings.get(path_hash, DEFAULT_LOCAL_SETTINGS)

    @inject_self.cached
    def save_global(self, settings: GlobalSettings | None = None) -> None:
        """Save global settings to disk."""
        self._global_settings = settings if settings is not None else self._global_settings

        try:
            GLOBAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            GLOBAL_SETTINGS_PATH.write_text(self._global_settings.model_dump_json(indent=2), encoding="utf-8")
            logger.debug("Saved global settings to: %s", GLOBAL_SETTINGS_PATH)
            self._signals.globalChanged.emit()
        except OSError as e:
            logger.exception("Failed to save global settings: %s", e)

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
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            logger.debug("Saved local settings for %s to: %s", script_path, settings_path)
            self._signals.localChanged.emit(str(settings_path))
        except OSError:
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
        if not GLOBAL_SETTINGS_PATH.exists():
            logger.info("Global settings file does not exist. Using defaults.")
            return

        try:
            data = json.loads(GLOBAL_SETTINGS_PATH.read_text(encoding="utf-8"))
            self._global_settings = GlobalSettings.model_validate(data)

            # Merge in any new shortcuts that don't exist in the loaded settings
            self._merge_default_shortcuts()

            logger.debug("Loaded global settings")
            logger.log(DEBUG - 1, " %s", lambda: pretty_repr(self._global_settings))
        except ValidationError as e:
            logger.error(
                "Global settings file is malformed. Using defaults.\nFile: %s\nError: %s",
                GLOBAL_SETTINGS_PATH.resolve(),
                e,
            )
        except json.JSONDecodeError:
            logger.exception("Failed to parse global settings JSON")

    def _merge_default_shortcuts(self) -> None:
        existing_action_ids = {s.action_id for s in self._global_settings.shortcuts}

        # Find shortcuts that exist in defaults but not in loaded settings
        missing_shortcuts = [
            shortcut for shortcut in DEFAULT_GLOBAL_SETTINGS.shortcuts if shortcut.action_id not in existing_action_ids
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

        fallback_settings = DEFAULT_LOCAL_SETTINGS.model_copy(update={"source_path": str(script_path)})

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
