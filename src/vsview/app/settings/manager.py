"""Settings manager for vsview."""

import json
import os
import shutil
import sys
from collections.abc import Callable
from copy import deepcopy
from inspect import Signature
from logging import DEBUG, getLogger
from pathlib import Path
from typing import Any, NamedTuple

from jetpytools import CustomTypeError, Singleton, inject_self
from pydantic import BaseModel, ValidationError
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QApplication
from rich.pretty import pretty_repr

from ...env import getenv_bool
from ..utils import WeakableSignals, atomic_write
from .models import GlobalSettings, LocalSettings

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
        self._baselines = dict[Path, _Baseline]()
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
            logger.debug("%s is not in loaded local settings (from %s)", path_hash, lambda: Path(script_path).name)
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
        path = Path(path or GlobalSettings.path_env)

        try:
            if not self._noop:
                self._global_settings = self._write(path, self._global_settings)
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

        script_path = Path(script_path)

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
                self._local_settings[path_to_hash(script_path)] = self._write(settings_path, resolved_settings)
                logger.debug("Saved local settings for %s to: %s", script_path, settings_path)
            self._signals.localChanged.emit(str(settings_path))
        except Exception:
            logger.exception("Failed to save local settings for %s", script_path)
            return False

        return True

    @staticmethod
    def local_settings_path(script_path: os.PathLike[str]) -> Path:
        """
        Get the file path for a script's local settings.

        Args:
            script_path: Path to the script file.

        Returns:
            Path to the local settings JSON file.
        """
        from ..utils import path_to_hash

        return Path(script_path).parent / ".vsjet" / "vsview" / f"{path_to_hash(script_path)}.json"

    def _load_global(self) -> None:
        if self._noop:
            logger.info("Loading with no config set")
            return

        # Always write the reference global settings file
        if not GlobalSettings.path.exists():
            with QSignalBlocker(self._signals):
                self.save_global(self.default_global_settings, GlobalSettings.path)

        # Taken before the read, so a write by another process after this point is seen when saving
        mtime = _mtime(GlobalSettings.path_env)

        if (settings := self._read_global()) is not None:
            self._global_settings = settings
            # Merge in any new shortcuts that don't exist in the loaded settings
            self._merge_default_shortcuts()

        self._remember(GlobalSettings.path_env, self._global_settings, mtime)

    def _read_global(self) -> GlobalSettings | None:
        # Determine which file to load (path_env == path when the env var is unset)
        path = GlobalSettings.path_env

        if not path.exists():
            # Env-scoped file doesn't exist.
            # Fallback to the reference file only if COPY is enabled, otherwise keep defaults
            if not getenv_bool("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"):
                logger.info("Global settings file does not exist. Using defaults.")
                return None

            path = GlobalSettings.path

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            settings, dropped = validate_lenient(GlobalSettings, data)
        except (OSError, ValidationError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
            logger.warning("Global settings file is malformed or unreadable. Using defaults.\nError: %s", e)
            logger.debug("Full traceback:", exc_info=True)
            _back_up_unreadable_file(path)
            return None
        except Exception:
            logger.exception("Unhandled exception:")
            return None

        if dropped:
            logger.warning("Invalid global settings were reset to their defaults: %s", ", ".join(dropped))
            _back_up_unreadable_file(path)

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

        script_path = Path(script_path)
        settings_path = self.local_settings_path(script_path)

        if self._noop:
            logger.info("Loading with no config set")
            self._local_settings[path_to_hash(script_path)] = self._default_local_settings(script_path)
            return

        # Taken before the read, so a write by another process after this point is seen when saving
        mtime = _mtime(settings_path)
        settings = self._read_local(script_path, settings_path)

        if not settings.source_path:
            settings.source_path = str(script_path)

        self._local_settings[path_to_hash(script_path)] = settings
        self._remember(settings_path, settings, mtime)

    def _read_local(self, script_path: Path, settings_path: Path) -> LocalSettings:
        if not settings_path.exists():
            logger.debug("Local settings file does not exist for %s. Using defaults.", script_path)
            return self._default_local_settings(script_path)

        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            settings, dropped = validate_lenient(LocalSettings, data)
        except (OSError, ValidationError, json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
            logger.error(
                "Local settings file is malformed or unreadable for %s. Using defaults.\nFile: %s\nError: %s",
                script_path,
                settings_path.resolve(),
                e,
            )
            logger.debug("Full traceback:", exc_info=True)
            _back_up_unreadable_file(settings_path)
            return self._default_local_settings(script_path)

        if dropped:
            logger.warning(
                "Invalid local settings for %s were reset to their defaults: %s",
                script_path,
                ", ".join(dropped),
            )
            _back_up_unreadable_file(settings_path)

        logger.debug("Loaded local settings for %s", script_path)
        logger.log(DEBUG - 1, "%s", lambda: pretty_repr(settings))
        return settings

    def _default_local_settings(self, script_path: Path) -> LocalSettings:
        return self.default_local_settings.model_copy(update={"source_path": str(script_path)})

    def _write[S: GlobalSettings | LocalSettings](self, path: Path, settings: S) -> S:
        """Write `settings`, after merging the changes another process saved since this one last read or wrote it."""
        text = settings.model_dump_json(indent=2)

        if (merged := self._merge_external_changes(path, settings, json.loads(text))) is not None:
            settings = merged
            text = settings.model_dump_json(indent=2)

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, text)
        self._baselines[path] = _Baseline(json.loads(text), _mtime(path))

        return settings

    def _merge_external_changes[S: GlobalSettings | LocalSettings](
        self, path: Path, settings: S, ours: dict[str, Any]
    ) -> S | None:
        if (baseline := self._baselines.get(path)) is None or _mtime(path) in (None, baseline.mtime_ns):
            return None

        try:
            theirs = json.loads(path.read_text(encoding="utf-8"))
            merged, _ = validate_lenient(type(settings), _merge_changes(baseline.data, ours, theirs))
        except (OSError, ValueError, TypeError):
            logger.warning("Couldn't merge the settings saved by another vsview window into %s", path, exc_info=True)
            return None

        logger.info("Merged the settings saved by another vsview window into %s", path)
        return merged

    def _remember(self, path: Path, settings: GlobalSettings | LocalSettings, mtime: int | None) -> None:
        self._baselines[path] = _Baseline(json.loads(settings.model_dump_json()), mtime)


class _Baseline(NamedTuple):
    data: dict[str, Any]
    """The settings as this process last read or wrote them."""

    mtime_ns: int | None
    """The file modification time at that moment, or None if the file did not exist."""


_MISSING = object()


def _merge_changes(base: Any, ours: Any, theirs: Any) -> Any:
    # A value changed by this process since `base` keeps its value. Every other value is taken from `theirs`.
    # Dicts are merged key by key. Lists and other values are replaced as a whole.
    if not (isinstance(ours, dict) and isinstance(theirs, dict)):
        return ours if ours != base else theirs

    base = base if isinstance(base, dict) else {}
    merged = {key: theirs[key] for key in theirs if key not in ours}

    for key, value in ours.items():
        merged[key] = _merge_changes(base.get(key, _MISSING), value, theirs[key]) if key in theirs else value

    return merged


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def validate_lenient[M: BaseModel](model_cls: type[M], data: Any) -> tuple[M, list[str]]:
    """
    Validate `data`, resetting each invalid field to its default instead of failing.

    Returns:
        The model and the dotted paths of the fields that were reset.

    Raises:
        ValidationError: An error cannot be fixed by resetting a field, for example a missing required field.
    """
    dropped = list[str]()

    while True:
        try:
            return model_cls.model_validate(data), dropped
        except ValidationError as e:
            paths = set[tuple[int | str, ...]]()

            for error in e.errors():
                if (path := _invalid_key_path(data, error["loc"])) is None:
                    raise
                paths.add(path)

            if not dropped:
                data = deepcopy(data)

            # Longest first, so dropping a parent key never comes before dropping one of its children
            for path in sorted(paths, key=len, reverse=True):
                _drop_key(data, path)
                dropped.append(".".join(map(str, path)))


def _invalid_key_path(data: Any, loc: tuple[int | str, ...]) -> tuple[int | str, ...] | None:
    # The deepest dict key on the error path. Dropping it makes the field use its default.
    # Parts that are not in the data (union members, validator names, missing fields) end the walk.
    node, path = data, None

    for i, part in enumerate(loc):
        if isinstance(node, dict) and part in node:
            path = loc[: i + 1]
        elif not (isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node)):
            break
        if isinstance(node, list) and isinstance(part, int):  # noqa: SIM114
            node = node[part]
        elif isinstance(node, dict) and isinstance(part, str):
            node = node[part]
        else:
            raise NotImplementedError

    return path


def _drop_key(data: Any, path: tuple[int | str, ...]) -> None:
    *parents, key = path

    for part in parents:
        data = data[part]

    data.pop(key, None)


def _back_up_unreadable_file(path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.bak")

    try:
        shutil.copy2(path, backup_path)
    except OSError:
        logger.exception("Failed to back up the settings file %s", path)
    else:
        logger.warning("Saved a copy of the unreadable settings file to %s", backup_path)


def _callable_one_param(obj: object) -> TypeIs[Callable[[LocalSettings], LocalSettings]]:
    return callable(obj) and len(Signature.from_callable(obj).parameters) == 1


def _callable_no_param(obj: object) -> TypeIs[Callable[[], LocalSettings]]:
    return callable(obj) and len(Signature.from_callable(obj).parameters) == 0
