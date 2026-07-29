from __future__ import annotations

import pkgutil
import weakref
from collections.abc import Callable, Set  # noqa: PYI025
from concurrent.futures import Future
from functools import wraps
from importlib import import_module
from inspect import ismethod
from logging import getLogger
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, Literal

import pluggy
from jetpytools import Singleton, inject_self
from pydantic import BaseModel, ValidationError
from vapoursynth import AudioNode, VideoNode
from vsengine.loops import EventLoop, get_loop

from ...vsenv import run_in_background
from .. import tools
from . import specs

if TYPE_CHECKING:
    from ..workspace import BaseWorkspace
    from .api import NodeProcessor, WidgetPluginBase

logger = getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self._callbacks = list[tuple[weakref.ReferenceType[Callable[[], Any]], EventLoop]]()
        self._ready = False
        self._lock = Lock()

    def register(self, callback: Callable[[], Any]) -> None:
        with self._lock:
            if self._ready:
                get_loop().from_thread(callback)
                return

            self._callbacks.append(
                (weakref.WeakMethod(callback) if ismethod(callback) else weakref.ref(callback), get_loop())
            )

    def notify(self) -> None:
        with self._lock:
            if self._ready:
                return

            self._ready = True
            current_callbacks = self._callbacks[:]
            self._callbacks.clear()

        for ref, loop in current_callbacks:
            if (cb := ref()) is not None:
                loop.from_thread(cb)


def ensure_loaded[T: PluginManager, **P, R](
    action: str,
) -> Callable[[Callable[Concatenate[T, P], R]], Callable[Concatenate[T, P], R]]:

    def decorator(func: Callable[Concatenate[T, P], R]) -> Callable[Concatenate[T, P], R]:
        @wraps(func)
        def wrapper(self: T, *args: P.args, **kwargs: P.kwargs) -> R:
            if action == "wait" and not self.loaded:
                self.load()
                self.wait_for_loaded()
            elif action == "entrypoints" and not self._entry_points_loaded:
                raise RuntimeError("PluginManager is not loaded yet")

            return func(self, *args, **kwargs)

        return wrapper

    return decorator


class PluginManager(Singleton):
    ALLOWED_WORKSPACES: ClassVar[Set[str]] = {"vsview_editor", "vsview_testwk"}

    def __init__(self) -> None:
        self.manager = pluggy.PluginManager("vsview")
        self._notifier = Notifier()
        self._settings_extracted = False
        self._entry_points_loaded = False
        self._load_future: Future[None] | None = None
        self._lock = Lock()

    @inject_self.cached.property
    @ensure_loaded("entrypoints")
    def tooldocks(self) -> list[type[WidgetPluginBase]]:
        return self.manager.hook.vsview_register_tooldock()

    @inject_self.cached.property
    @ensure_loaded("entrypoints")
    def toolpanels(self) -> list[type[WidgetPluginBase]]:
        return self.manager.hook.vsview_register_toolpanel()

    @inject_self.cached.property
    @ensure_loaded("entrypoints")
    def video_processor(self) -> type[NodeProcessor[VideoNode]] | None:
        return self.manager.hook.vsview_get_video_processor()

    @inject_self.cached.property
    @ensure_loaded("entrypoints")
    def audio_processor(self) -> type[NodeProcessor[AudioNode]] | None:
        return self.manager.hook.vsview_get_audio_processor()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def all_plugins(self) -> list[type[WidgetPluginBase | NodeProcessor[Any]]]:
        all_plugins: set[Any] = {*self.tooldocks, *self.toolpanels}

        if vp := self.video_processor:
            all_plugins.add(vp)
        if ap := self.audio_processor:
            all_plugins.add(ap)

        return sorted(all_plugins, key=lambda p: p.identifier)

    @inject_self.cached.property
    @ensure_loaded("entrypoints")
    def workspaces(self) -> list[type[BaseWorkspace]]:
        restricted_caller = self.manager.subset_hook_caller(
            "vsview_register_workspace",
            remove_plugins=(p for n, p in self.manager.list_name_plugin() if n not in self.ALLOWED_WORKSPACES),
        )

        return restricted_caller()

    @inject_self.property
    def loaded(self) -> bool:
        return self._load_future is not None and self._load_future.done()

    @inject_self
    def load(self) -> None:
        if self._load_future:
            return

        self._load_future = self._load_worker()

    @inject_self
    def wait_for_loaded(self) -> None:
        if self._load_future:
            self._load_future.result()

    @inject_self
    def call_when_loaded(self, cb: Callable[[], Any]) -> None:
        self._notifier.register(cb)

    @run_in_background(name="PluginManagerLoad")
    def _load_worker(self) -> None:
        self.manager.add_hookspecs(specs)

        for _, name, _ in pkgutil.iter_modules(tools.__path__):
            if name.startswith("_"):
                continue
            logger.debug("Registering %s first party plugin", name)
            self.manager.register(import_module(f"vsview.app.tools.{name}"))

        logger.debug("Loading entrypoints...")
        try:
            n = self.manager.load_setuptools_entrypoints("vsview")
        except BaseException:
            logger.exception("Couldn't load entry points!")
            n = 0

        logger.debug("Loaded %d second/third party plugins", n)
        self._entry_points_loaded = True

        self._register_shortcuts()
        self._construct_settings_registry()

        logger.debug("Plugin integration, finalized")

        # Fire signal
        self._notifier.notify()

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

        # FIXME
        # ShortcutManager._check_conflicts()

    def _construct_settings_registry(self) -> None:
        from ..settings.dialog import SettingsDialog
        from ..settings.models import SettingEntry, extract_settings

        def extract_plugin_settings(model: type, plugin_id: str, section_prefix: str) -> list[SettingEntry]:
            return [
                entry._replace(key=f"plugins.{plugin_id}.{entry.key}")
                for entry in extract_settings(model, section_prefix=section_prefix)
            ]

        global_entries = list[SettingEntry]()
        local_entries = list[SettingEntry]()

        for plugin in self.all_plugins:
            global_model = plugin.global_settings_model
            local_model = plugin.local_settings_model
            identifier = plugin.identifier
            display_name = plugin.display_name

            if global_model is None and local_model is None:
                continue

            section_prefix = f"Plugin - {display_name}"

            if global_model is not None:
                global_entries.extend(extract_plugin_settings(global_model, identifier, section_prefix))

            if local_model is not None:
                local_entries.extend(extract_plugin_settings(local_model, identifier, section_prefix))

        self._populate_default_settings("global")

        # Extend dialog registries
        SettingsDialog.global_settings_registry.extend(global_entries)
        SettingsDialog.local_settings_registry.extend(local_entries)

        logger.debug("Plugin settings extracted")

    @inject_self
    @ensure_loaded("wait")
    def populate_default_settings(self, scope: Literal["global", "local"], file_path: Path | None = None) -> None:
        self._populate_default_settings(scope, file_path)

    def _populate_default_settings(self, scope: Literal["global", "local"], file_path: Path | None = None) -> None:
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
                try:
                    validated = model.model_validate(existing)
                except ValidationError:
                    logger.exception("The plugin %r has invalid settings:", plugin.identifier)
                    continue

                settings_container.plugins[plugin.identifier] = validated
