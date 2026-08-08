from collections.abc import Sequence
from typing import Any, ClassVar, Generic

from vsview.app.settings.action import ActionDefinition
from vsview.app.workspace import BaseWorkspace, LoaderWorkspace

from ._interface import _PluginBaseMeta
from .api import PluginSecrets, PluginSettings, TGlobalSettings, TLocalSettings


class PluginBaseWorkspace(BaseWorkspace, Generic[TGlobalSettings, TLocalSettings], metaclass=_PluginBaseMeta):  # noqa: UP046
    __plugin_base__ = True

    identifier: ClassVar[str]
    """Unique identifier for the plugin."""

    display_name: ClassVar[str]
    """Display name for the plugin."""

    shortcuts: ClassVar[Sequence[ActionDefinition]] = ()
    """
    Keyboard shortcuts for this workspace.

    Each ActionDefinition ID must start with "{identifier}." prefix.
    """


class PluginWorkspace(
    LoaderWorkspace[Any],
    PluginBaseWorkspace[TGlobalSettings, TLocalSettings],
    metaclass=_PluginBaseMeta,
):
    __plugin_base__ = True

    @property
    def settings(self) -> PluginSettings[TGlobalSettings, TLocalSettings]:
        """Get the settings wrapper for lazy, always-fresh access."""
        return PluginSettings(self)

    @property
    def secrets(self) -> PluginSecrets:
        """Get a namespaced secure secrets API for this plugin."""
        return PluginSecrets(self)

    def update_global_settings(self, **updates: Any) -> None:
        """Update specific global settings fields and trigger persistence."""
        self.api._update_settings(self, "global", **updates)

    def update_local_settings(self, **updates: Any) -> None:
        """Update specific local settings fields and trigger persistence."""
        self.api._update_settings(self, "local", **updates)
