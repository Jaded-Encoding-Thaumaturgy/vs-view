from __future__ import annotations

from concurrent.futures import Future
from importlib import import_module
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal

import pluggy
from jetpytools import Singleton, inject_self
from pydantic import BaseModel
from PySide6.QtCore import QObject, Signal
from vapoursynth import AudioNode, VideoNode

from ...vsenv import run_in_background
from . import specs

if TYPE_CHECKING:
    from .api import NodeProcessor, WidgetPluginBase

logger = getLogger(__name__)


class PluginSignals(QObject):
    pluginsLoaded = Signal()


class PluginManager(Singleton):
    def __init__(self) -> None:
        self.manager = pluggy.PluginManager("vsview")
        self._signals = PluginSignals()
        self._settings_extracted = False
        self._load_future: Future[None] | None = None
        self._lock = Lock()

    @inject_self.cached.property
    def tooldocks(self) -> list[type[WidgetPluginBase]]:
        return self.manager.hook.vsview_register_tooldock()

    @inject_self.cached.property
    def toolpanels(self) -> list[type[WidgetPluginBase]]:
        return self.manager.hook.vsview_register_toolpanel()

    @inject_self.cached.property
    def video_processor(self) -> type[NodeProcessor[VideoNode]] | None:
        return self.manager.hook.vsview_get_video_processor()

    @inject_self.cached.property
    def audio_processor(self) -> type[NodeProcessor[AudioNode]] | None:
        return self.manager.hook.vsview_get_audio_processor()

    @inject_self.property
    def all_plugins(self) -> list[type[WidgetPluginBase | NodeProcessor[Any]]]:
        all_plugins: list[Any] = [*self.tooldocks, *self.toolpanels]

        if vp := self.video_processor:
            all_plugins.append(vp)
        if ap := self.audio_processor:
            all_plugins.append(ap)

        return all_plugins

    @inject_self.property
    def settings_extracted(self) -> bool:
        return self._settings_extracted

    @inject_self.property
    def loaded(self) -> bool:
        return self._load_future is not None and self._load_future.done()

    @inject_self.property
    def signals(self) -> PluginSignals:
        return self._signals

    @inject_self
    def load(self) -> None:
        if self._load_future:
            return

        self._load_future = self._load_worker()

    @inject_self
    def wait_for_loaded(self) -> None:
        if self._load_future:
            self._load_future.result()

    @run_in_background(name="PluginManagerLoad")
    def _load_worker(self) -> None:
        self.manager.add_hookspecs(specs)

        for path in (Path(__file__).parent.parent / "tools").glob("*"):
            if path.stem.startswith("_"):
                continue
            logger.debug("Registering %s", lambda: path.name)
            self.manager.register(import_module(f"vsview.app.tools.{path.stem}"))

        logger.debug("Loading entrypoints...")
        n = self.manager.load_setuptools_entrypoints("vsview")

        self._register_shortcuts()
        self._construct_settings_registry()

        logger.debug("Loaded %d third party plugins", n)
        self._signals.pluginsLoaded.emit()

    def _register_shortcuts(self) -> None:
        from ..settings.shortcuts import ShortcutManager

        for plugin in self.all_plugins:
            if not (shortcuts := getattr(plugin, "shortcuts", ())):
                continue

            expected_prefix = f"{plugin.identifier}."
            valid_definitions = []

            for definition in shortcuts:
                if not definition.startswith(expected_prefix):
                    logger.warning(
                        "Plugin %r has shortcut %r without proper namespace prefix. "
                        "Expected prefix: %r. Shortcut will be ignored.",
                        plugin.identifier,
                        str(definition),
                        expected_prefix,
                    )
                    continue
                valid_definitions.append(definition)

            if valid_definitions:
                ShortcutManager.register_definitions(valid_definitions)

        ShortcutManager._check_conflicts()

    def _construct_settings_registry(self) -> None:
        from ..settings.dialog import SettingsDialog
        from ..settings.models import SettingEntry, extract_settings

        def extract_plugin_settings(model: type | None, plugin_id: str, section_name: str) -> list[SettingEntry]:
            if model is None:
                return []
            return [
                entry._replace(key=f"plugins.{plugin_id}.{entry.key}")
                for entry in extract_settings(model, section=section_name)
            ]

        global_entries = list[SettingEntry]()
        local_entries = list[SettingEntry]()

        for plugin in self.all_plugins:
            global_model = plugin.global_settings_model
            local_model = plugin.local_settings_model
            identifier = plugin.identifier
            display_name = plugin.display_name

            if global_model is None:
                continue

            section = f"Plugin - {display_name}"

            global_entries.extend(extract_plugin_settings(global_model, identifier, section))
            local_entries.extend(extract_plugin_settings(local_model, identifier, section))

        self.populate_default_settings("global")

        # Extend dialog registries
        SettingsDialog.global_settings_registry.extend(global_entries)
        SettingsDialog.local_settings_registry.extend(local_entries)

        self._settings_extracted = True
        logger.debug("Plugin settings extracted")

    @inject_self
    def populate_default_settings(self, scope: Literal["global", "local"], file_path: Path | None = None) -> None:
        from ..settings import SettingsManager

        if scope == "local" and file_path is not None:
            settings_container = SettingsManager.get_local_settings(file_path)
        else:
            settings_container = SettingsManager.global_settings

        model_attr = f"{scope}_settings_model"

        with self._lock:
            for plugin in self.all_plugins:
                if (model := getattr(plugin, model_attr)) is None:
                    continue

                raw = settings_container.plugins.get(plugin.identifier, {})
                existing = raw.model_dump() if isinstance(raw, BaseModel) else raw

                # Validate existing settings (missing fields will be filled with defaults by Pydantic)
                settings_container.plugins[plugin.identifier] = model.model_validate(existing)
