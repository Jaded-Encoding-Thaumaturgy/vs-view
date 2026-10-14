from __future__ import annotations

from collections.abc import Callable
from itertools import zip_longest
from logging import getLogger
from pathlib import Path
from types import get_original_bases
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, get_args, get_origin, override
from weakref import WeakKeyDictionary

from pydantic import BaseModel
from PySide6.QtCore import QMetaObject, QObject, SignalInstance
from PySide6.QtWidgets import QWidget

from vsview.app.settings import SecretsManager, SettingsManager
from vsview.app.utils import ObjectType

from .contracts import LocalSettingsModel

if TYPE_CHECKING:
    from vsview.app.views.timeline import Timeline
    from vsview.app.views.video import GraphicsView
    from vsview.app.workspace.playback import PlaybackManager

    from .api import PluginAPI

logger = getLogger(__name__)


class _PluginLike(Protocol):
    identifier: ClassVar[str]
    api: PluginAPI


class _SettingsProxy[T: BaseModel]:
    """
    Proxy that intercepts `__setattr__` on a pydantic BaseModel and auto-persists the change via the supplied callback.
    Reads are delegated transparently to the underlying model.
    """

    __slots__ = ("_model", "_on_update")

    def __init__(self, model: T, on_update: Callable[[str, Any], None]) -> None:
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_on_update", on_update)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._model, name)
        return _SettingsProxy(attr, lambda _, __: self._on_update(name, attr)) if isinstance(attr, BaseModel) else attr

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        elif hasattr(self._model, name):
            if isinstance(value, _SettingsProxy):
                value = value._model

            setattr(self._model, name, value)
            self._on_update(name, value)
        else:
            raise AttributeError(f"{type(self._model).__name__!r} object has no attribute {name!r}")

    @override
    def __repr__(self) -> str:
        return repr(self._model)

    @override
    def __eq__(self, other: object) -> bool:
        return self._model == other._model if isinstance(other, _SettingsProxy) else self._model == other


class _PluginSettingsStore:
    def __init__(self, source: Callable[[], Path | None]) -> None:
        self._source = source
        self._caches: dict[str, WeakKeyDictionary[_PluginLike, BaseModel]] = {
            "global": WeakKeyDictionary(),
            "local": WeakKeyDictionary(),
        }

    @property
    def file_path(self) -> Path | None:
        return self._source()

    def get(self, plugin: _PluginLike, scope: str) -> BaseModel | None:
        if scope not in ("global", "local"):
            raise ValueError(f"Invalid settings scope {scope!r}, must be 'global' or 'local'.")

        cache = self._caches[scope]

        if plugin in cache:
            return cache[plugin]

        model_cls: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model", None)
        if model_cls is None:
            return None

        # Fetch and validate
        settings = model_cls.model_validate(self._get_raw_settings(plugin.identifier, scope))

        # Resolve local settings with global fallbacks
        if (
            scope == "local"
            and isinstance(settings, LocalSettingsModel)
            and (global_settings := self.get(plugin, "global"))
        ):
            settings = settings.resolve(global_settings)

        cache[plugin] = settings
        return settings

    def get_proxy(self, plugin: _PluginLike, scope: str) -> _SettingsProxy[BaseModel] | None:
        model = self.get(plugin, scope)
        if model is None:
            return None
        return _SettingsProxy(model, lambda k, v: self.update(plugin, scope, **{k: v}))

    def update(self, plugin: _PluginLike, scope: str, **updates: Any) -> None:
        if scope not in ("global", "local"):
            raise ValueError(f"Invalid settings scope {scope!r}, must be 'global' or 'local'.")

        model_cls: type[BaseModel] | None = getattr(plugin, f"{scope}_settings_model", None)
        if model_cls is None:
            raise ValueError(f"Plugin {plugin.identifier!r} has no {scope}_settings_model defined.")

        # Always update against the raw (unresolved) state
        raw_data = self._get_raw_settings(plugin.identifier, scope)
        settings = model_cls.model_validate(raw_data)

        for key, value in updates.items():
            setattr(settings, key, value)

        self._set_raw_settings(plugin.identifier, scope, settings)
        self._caches[scope].pop(plugin, None)

    def invalidate(self, scope: str) -> None:
        self._caches[scope].clear()

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


class _PluginBaseMeta(ObjectType):
    global_settings_model: type[BaseModel] | None
    local_settings_model: type[BaseModel] | None

    def __new__[MetaSelf: _PluginBaseMeta](  # noqa: PYI019
        mcls: type[MetaSelf],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        /,
        **kwargs: Any,
    ) -> MetaSelf:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)

        # Skip processing for base classes that set __plugin_base__ = True
        if namespace.get("__plugin_base__"):
            return cls

        # WidgetPluginBase and NodeProcessor are now defined so it's safe to import them
        from .api import NodeProcessor, WidgetPluginBase
        from .api_wk import PluginBaseWorkspace

        for base in get_original_bases(cls):
            if not (origin := get_origin(base)):
                continue

            args = get_args(base)

            if issubclass(origin, (WidgetPluginBase, PluginBaseWorkspace)):
                scope = ["global", "local"]
            elif issubclass(origin, NodeProcessor):
                scope = [None, "global", "local"]
            else:
                continue

            for n, arg in zip_longest(scope, args, fillvalue=None):
                if n is None:
                    continue
                if arg is None or not isinstance(arg, type):
                    setattr(cls, f"{n}_settings_model", None)
                    continue

                origin = get_origin(arg)
                is_basemodel = (origin is None and issubclass(arg, BaseModel)) or (
                    origin is not None and issubclass(origin, BaseModel)
                )
                setattr(cls, f"{n}_settings_model", arg if is_basemodel else None)
            break
        else:
            cls.global_settings_model, cls.local_settings_model = None, None

        return cls


class _GraphicsViewProxy(QObject):
    def __init__(self, tab_changed: SignalInstance, view: GraphicsView) -> None:
        super().__init__()
        self.__tab_changed = tab_changed
        self.__view = view


class _ViewportProxy(QObject):
    def __init__(self, tab_changed: SignalInstance, viewport: QWidget) -> None:
        super().__init__()
        self.__tab_changed = tab_changed
        self.__viewport = viewport
        self.__cursor_reset_conn: QMetaObject.Connection | None = None


class _TimelineProxy(QObject):
    def __init__(self, timeline: Timeline) -> None:
        super().__init__()
        self.__timeline = timeline


class _PlaybackProxy(QObject):
    def __init__(self, playback_manager: PlaybackManager) -> None:
        super().__init__()
        self.__playback_manager = playback_manager


class _PluginSecrets:
    def __init__(self, plugin: _PluginLike) -> None:
        self.__namespace = plugin.identifier

    def __getattr__(self, name: str) -> Any:
        if name in dir(SecretsManager) and not name.startswith("_"):
            return lambda *args, **kwargs: getattr(SecretsManager, name)(self.__namespace, *args, **kwargs)

        raise AttributeError
