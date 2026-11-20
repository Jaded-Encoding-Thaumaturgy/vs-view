from __future__ import annotations

import pkgutil
import weakref
from collections import defaultdict
from collections.abc import Callable, Set  # noqa: PYI025
from concurrent.futures import Future
from functools import wraps
from importlib import import_module
from inspect import ismethod
from logging import getLogger
from threading import Lock
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate, cast

import pluggy
from jetpytools import Singleton, inject_self
from vapoursynth import AudioNode, VideoNode
from vsengine.loops import EventLoop, get_loop

from ...vsenv import run_in_background
from .. import tools
from . import specs
from .entrypoints import load_entrypoints

if TYPE_CHECKING:
    from ..settings.models import GlobalSettings, LocalSettings, SettingEntry
    from .api import NodeProcessor, WidgetPluginBase
    from .api_wk import PluginBaseWorkspace

logger = getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self._callbacks = list[tuple[weakref.WeakMethod[Callable[[], Any]] | Callable[[], Any], EventLoop]]()
        self._ready = False
        self._lock = Lock()

    def register(self, callback: Callable[[], Any]) -> None:
        with self._lock:
            if self._ready:
                get_loop().from_thread(callback)
                return

            self._callbacks.append((weakref.WeakMethod(callback) if ismethod(callback) else callback, get_loop()))

    def notify(self) -> None:
        with self._lock:
            if self._ready:
                return

            self._ready = True
            current_callbacks = self._callbacks[:]
            self._callbacks.clear()

        for ref, loop in current_callbacks:
            if (cb := ref() if isinstance(ref, weakref.WeakMethod) else ref) is not None:
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
    ALLOWED_WORKSPACES: ClassVar[Set[str]] = {"vsview_editor", "vsview_remote"}

    def __init__(self) -> None:
        self.manager = pluggy.PluginManager("vsview")
        self._notifier = Notifier()
        self._settings_extracted = False
        self._entry_points_loaded = False
        self._load_future: Future[None] | None = None
        self._lock = Lock()
        self._plugin_global_settings: list[SettingEntry] | None = None
        self._plugin_local_settings: list[SettingEntry] | None = None

    @inject_self.property
    @ensure_loaded("entrypoints")
    def tooldocks(self) -> list[type[WidgetPluginBase[Any, Any]]]:
        return self.manager.hook.vsview_register_tooldock()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def toolpanels(self) -> list[type[WidgetPluginBase[Any, Any]]]:
        return self.manager.hook.vsview_register_toolpanel()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def video_processor(self) -> type[NodeProcessor[VideoNode, Any, Any]] | None:
        return self.manager.hook.vsview_get_video_processor()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def audio_processor(self) -> type[NodeProcessor[AudioNode, Any, Any]] | None:
        return self.manager.hook.vsview_get_audio_processor()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def all_plugins(
        self,
    ) -> list[type[WidgetPluginBase[Any, Any] | NodeProcessor[Any] | PluginBaseWorkspace[Any, Any]]]:
        return self._all_plugins()

    @inject_self.property
    @ensure_loaded("entrypoints")
    def workspaces(self) -> list[type[PluginBaseWorkspace[Any, Any]]]:
        return self._workspaces()

    @inject_self.property
    def plugin_global_settings(self) -> list[SettingEntry]:
        return self._plugin_global_settings or []

    @inject_self.property
    def plugin_local_settings(self) -> list[SettingEntry]:
        return self._plugin_local_settings or []

    @inject_self.property
    def loaded(self) -> bool:
        return self._load_future is not None and self._load_future.done() and self._load_future.exception() is None

    @inject_self.property
    def load_failed(self) -> bool:
        return self._load_future is not None and self._load_future.done() and self._load_future.exception() is not None

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
        if self._load_future is not None and self._load_future.done() and (e := self._load_future.exception()):
            logger.warning("Plugin loading failed (%s); calling %r with the plugins that loaded", e, cb)

        self._notifier.register(cb)

    @property
    def _plugin_identifiers(self) -> dict[str, str]:
        return {identifier: min(owners) for identifier, owners in self._identifier_owners().items()}

    def _all_plugins(
        self,
    ) -> list[type[WidgetPluginBase[Any, Any] | NodeProcessor[Any] | PluginBaseWorkspace[Any, Any]]]:
        hook = self.manager.hook
        all_plugins: set[Any] = {
            *hook.vsview_register_tooldock(),
            *hook.vsview_register_toolpanel(),
            *self._workspaces(),
        }

        if vp := hook.vsview_get_video_processor():
            all_plugins.add(vp)
        if ap := hook.vsview_get_audio_processor():
            all_plugins.add(ap)

        return sorted(all_plugins, key=lambda p: p.identifier)

    def _workspaces(self) -> list[type[PluginBaseWorkspace[Any, Any]]]:
        restricted_caller = self.manager.subset_hook_caller(
            "vsview_register_workspace",
            remove_plugins=(p for n, p in self.manager.list_name_plugin() if n not in self.ALLOWED_WORKSPACES),
        )

        return restricted_caller()

    def _identifier_owners(self) -> dict[str, set[str]]:
        owners = defaultdict[str, set[str]](set)
        for hook_name in self.manager.hook.__dict__:
            hc: pluggy.HookCaller = getattr(self.manager.hook, hook_name)
            for hookimpl in hc.get_hookimpls():
                try:
                    res = cast(type[Any], hookimpl.function())
                    if res:
                        owners[res.identifier].add(hookimpl.plugin_name)
                except Exception:
                    logger.exception("Failed to inspect hook %r in plugin %r", hook_name, hookimpl.plugin_name)
                    self.manager.unregister(name=hookimpl.plugin_name)

        return {
            identifier: alive
            for identifier, names in owners.items()
            if (alive := {name for name in names if self.manager.has_plugin(name)})
        }

    @run_in_background(name="PluginManagerLoad")
    def _load_worker(self) -> None:
        try:
            self._load_plugins()
        finally:
            self._entry_points_loaded = True
            self._notifier.notify()

    def _load_plugins(self) -> None:
        self.manager.add_hookspecs(specs)

        for _, name, _ in pkgutil.iter_modules(tools.__path__):
            if name.startswith("_"):
                continue
            logger.debug("Registering %s first party plugin", name)
            try:
                self.manager.register(import_module(f"vsview.app.tools.{name}"))
            except Exception:
                logger.exception("Couldn't register first party plugin %r", name)

        first_party_names = {name for names in self._identifier_owners().values() for name in names}

        logger.debug("Loading entrypoints...")
        n = load_entrypoints(self.manager, "vsview")
        logger.debug("Loaded %d second/third party plugins", n)

        self._reject_invalid_identifiers(first_party_names)
        self._register_shortcuts()
        self._construct_settings_registry()

        logger.debug("Plugin integration, finalized")

    def _reject_invalid_identifiers(self, first_party_names: Set[str]) -> None:
        for identifier, names in self._identifier_owners().items():
            if "." not in identifier:
                continue
            for name in sorted(names):
                logger.warning("Skipping plugin %r because its identifier %r contains a '.'", name, identifier)
                self.manager.unregister(name=name)

        for identifier, names in self._identifier_owners().items():
            if len(names) < 2 or not (third_party := sorted(names - first_party_names)):
                continue

            if names & first_party_names:
                for name in third_party:
                    logger.warning(
                        "Skipping third-party plugin %r because it reuses first-party plugin identifier %r",
                        name,
                        identifier,
                    )
            else:
                logger.warning(
                    "Skipping plugins %s because they share the plugin identifier %r",
                    ", ".join(repr(name) for name in third_party),
                    identifier,
                )

            for name in third_party:
                self.manager.unregister(name=name)

    def _register_shortcuts(self) -> None:
        from ..settings.shortcuts import ShortcutManager

        def register(plugin: type[Any]) -> None:
            if not (shortcuts := getattr(plugin, "shortcuts", ())):
                return

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

        self._for_each_plugin("registering its shortcuts", register)

    def _construct_settings_registry(self) -> None:
        from ..settings import SettingsManager
        from ..settings.dialog import PLUGIN_PREFIX
        from ..settings.models import SettingEntry, extract_settings

        def extract_plugin_settings(model: type, plugin_id: str, section_prefix: str) -> list[SettingEntry]:
            return [
                entry._replace(key=f"plugins.{plugin_id}.{entry.key}")
                for entry in extract_settings(model, section_prefix=section_prefix)
            ]

        global_entries = list[SettingEntry]()
        local_entries = list[SettingEntry]()

        def extract(plugin: type[Any]) -> None:
            global_model = plugin.global_settings_model
            local_model = plugin.local_settings_model
            identifier = plugin.identifier
            display_name = plugin.display_name

            if global_model is None and local_model is None:
                return

            section_prefix = f"{PLUGIN_PREFIX} - {display_name}"

            plugin_global = (
                extract_plugin_settings(global_model, identifier, section_prefix) if global_model is not None else []
            )
            plugin_local = (
                extract_plugin_settings(local_model, identifier, section_prefix) if local_model is not None else []
            )
            global_entries.extend(plugin_global)
            local_entries.extend(plugin_local)

        self._for_each_plugin("extracting its settings", extract)

        self._populate_default_settings(SettingsManager.global_settings)
        self._plugin_global_settings = global_entries
        self._plugin_local_settings = local_entries

        logger.debug("Plugin settings extracted")

    def _for_each_plugin(self, step: str, func: Callable[[type[Any]], None]) -> None:
        owners = self._plugin_identifiers

        for plugin in self._all_plugins():
            owner = owners.get(plugin.identifier)

            if owner is not None and not self.manager.has_plugin(owner):
                continue

            try:
                func(plugin)
            except Exception:
                logger.exception(
                    "Plugin %r failed while %s. It will be unregistered.",
                    owner or plugin.identifier,
                    step,
                )
                if owner is not None:
                    self.manager.unregister(name=owner)

    @inject_self
    @ensure_loaded("wait")
    def populate_default_settings(self, container: GlobalSettings | LocalSettings) -> None:
        """Validate the plugin entries of `container`, and add default settings for plugins without one."""
        self._populate_default_settings(container)

    def _populate_default_settings(self, container: GlobalSettings | LocalSettings) -> None:
        from ..settings.models import GlobalSettings

        scope = "global" if isinstance(container, GlobalSettings) else "local"

        with self._lock:
            for plugin in self._all_plugins():
                if (model_cls := getattr(plugin, f"{scope}_settings_model")) is None:
                    continue
                try:
                    container.get_plugin_model(plugin.identifier, model_cls)
                except Exception:
                    logger.exception("Couldn't create the %s settings of plugin %r", scope, plugin)
