from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from pydantic import BaseModel

from vsview.app.settings import SettingsManager

if TYPE_CHECKING:
    from vsview.app.workspace.loader import LoaderWorkspace

    from .api import PluginBase


class PluginSettingsStore:
    def __init__(self, workspace: LoaderWorkspace[Any]) -> None:
        self._workspace = workspace
        self._global_cache: WeakKeyDictionary[PluginBase[Any, Any], BaseModel] = WeakKeyDictionary()
        self._local_cache: WeakKeyDictionary[PluginBase[Any, Any], BaseModel] = WeakKeyDictionary()

    @property
    def file_path(self) -> Path | None:
        from vsview.app.workspace.file import GenericFileWorkspace

        return self._workspace.content if isinstance(self._workspace, GenericFileWorkspace) else None

    def get(self, plugin: PluginBase[Any, Any], scope: str) -> BaseModel | None:
        cache = self._global_cache if scope == "global" else self._local_cache

        if plugin in cache:
            return cache[plugin]

        # Get the settings model for this scope
        model: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model")

        if model is None:
            return None

        # Fetch raw data from storage + validate into model
        settings = model.model_validate(self._get_raw_settings(plugin.identifier, scope))

        # Resolve local settings with global fallbacks
        from .api import LocalSettingsModel

        if (
            scope == "local"
            and isinstance(settings, LocalSettingsModel)
            and (global_settings := self.get(plugin, "global")) is not None
        ):
            settings = settings.resolve(global_settings)

        cache[plugin] = settings
        return settings

    def update(self, plugin: PluginBase[Any, Any], scope: str, **updates: Any) -> None:
        # For local settings, we need to update the raw (unresolved) settings,
        # not the resolved version with global fallbacks merged in.
        if (settings := self._get_unresolved_settings(plugin, scope)) is None:
            return

        # Apply updates to the settings object
        for key, value in updates.items():
            setattr(settings, key, value)

        # Persist to storage
        self._set_raw_settings(plugin.identifier, scope, settings)

        # Invalidate cache so next access re-validates
        cache = self._global_cache if scope == "global" else self._local_cache
        cache.pop(plugin, None)

    def invalidate(self, scope: str) -> None:
        getattr(self, f"_{scope}_cache").clear()

    def _get_raw_settings(self, plugin_id: str, scope: str) -> dict[str, Any]:
        if scope == "global":
            container = SettingsManager.global_settings
        elif self.file_path is not None:
            container = SettingsManager.get_local_settings(self.file_path)
        else:
            return {}

        raw = container.plugins.get(plugin_id, {})

        return raw if isinstance(raw, dict) else raw.model_dump()

    def _set_raw_settings(self, plugin_id: str, scope: str, settings: BaseModel) -> None:
        if scope == "global":
            SettingsManager.global_settings.plugins[plugin_id] = settings
        elif self.file_path is not None:
            SettingsManager.get_local_settings(self.file_path).plugins[plugin_id] = settings

    def _get_unresolved_settings(self, plugin: PluginBase[Any, Any], scope: str) -> BaseModel | None:
        model: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model")

        if model is None:
            return None

        return model.model_validate(self._get_raw_settings(plugin.identifier, scope))
