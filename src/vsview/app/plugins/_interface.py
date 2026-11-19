from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from itertools import zip_longest
from logging import getLogger
from threading import Lock
from types import get_original_bases
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, get_args, get_origin, override
from weakref import ref

from pydantic import BaseModel, ValidationError
from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtWidgets import QWidget
from shiboken6 import Shiboken

from vsview.app.settings import SecretsManager, SettingsManager
from vsview.app.settings.models import GlobalSettings, LocalSettings
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
    Live settings view.

    Writes validate and update the model, falling back to defaults/global on None reads.
    """

    __slots__ = ("_fallback", "_model", "_writable")

    if TYPE_CHECKING:
        _fallback: BaseModel | None
        _model: T
        _writable: bool

    def __init__(self, model: T, fallback: BaseModel | None = None, *, writable: bool = True) -> None:
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_fallback", fallback)
        object.__setattr__(self, "_writable", writable)

    def __getattr__(self, name: str) -> Any:
        # copy and pickle look up these names before __init__ has run
        if name in _SettingsProxy.__slots__:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

        value = getattr(self._model, name)

        if self._fallback is None:
            return _SettingsProxy(value, writable=self._writable) if isinstance(value, BaseModel) else value

        fallback = getattr(self._fallback, name, None)

        if isinstance(value, BaseModel):
            return _SettingsProxy(value, fallback if isinstance(fallback, BaseModel) else None, writable=self._writable)

        if value is None and fallback is not None:
            # The global value is shown read-only or as a copy, so a write through it cannot change the global settings
            return _SettingsProxy(fallback, writable=False) if isinstance(fallback, BaseModel) else deepcopy(fallback)

        return value

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if name in _SettingsProxy.__slots__:
            object.__setattr__(self, name, value)
        elif name not in type(self._model).model_fields:
            raise AttributeError(f"{type(self._model).__name__!r} object has no attribute {name!r}")
        elif not self._writable:
            raise TypeError(f"Cannot set {name!r}: the local value is None, so this shows the global value")
        else:
            self._model.__pydantic_validator__.validate_assignment(self._model, name, _unwrap(value))

    @override
    def __repr__(self) -> str:
        return repr(self._model)

    @override
    def __eq__(self, other: object) -> bool:
        return self._model == _unwrap(other)


class _PluginSettingsStore:
    """Reads and writes uncached plugin models directly in `settings.plugins`."""

    def __init__(self, local_settings: Callable[[], LocalSettings]) -> None:
        self._local_settings = local_settings

    def get_proxy(self, plugin: _PluginLike, scope: str) -> _SettingsProxy[BaseModel] | None:
        if (model := self._get_model(plugin, scope)) is None:
            return None

        fallback = self._get_model(plugin, "global") if isinstance(model, LocalSettingsModel) else None
        return _SettingsProxy(model, fallback)

    def update(self, plugin: _PluginLike, scope: str, **updates: Any) -> None:
        if (model := self._get_model(plugin, scope)) is None:
            raise ValueError(f"Plugin {plugin.identifier!r} has no {scope}_settings_model defined.")

        # One assignment is validated before it is stored. Several are first tried on a copy,
        # so a bad value in any of them leaves the model unchanged.
        if len(updates) > 1:
            _assign(model.model_copy(deep=True), updates)
        _assign(model, updates)

    def _get_model(self, plugin: _PluginLike, scope: str) -> BaseModel | None:
        if scope == "global":
            container: GlobalSettings | LocalSettings = SettingsManager.global_settings
        elif scope == "local":
            container = self._local_settings()
        else:
            raise ValueError(f"Invalid settings scope {scope!r}, must be 'global' or 'local'.")

        return get_plugin_model(container, plugin.identifier, getattr(plugin, f"{scope}_settings_model", None))


_materialize_lock = Lock()


def get_plugin_model(
    container: GlobalSettings | LocalSettings, identifier: str, model_cls: type[BaseModel] | None
) -> BaseModel | None:
    """Return the validated plugin model from `container.plugins`, initializing or defaulting invalid raw entries."""
    if model_cls is None:
        return None

    if isinstance(entry := container.plugins.get(identifier), model_cls):
        return entry

    with _materialize_lock:
        # Another thread may have converted the entry while this one waited
        if isinstance(entry := container.plugins.get(identifier), model_cls):
            return entry

        raw = entry.model_dump() if isinstance(entry, BaseModel) else entry or {}

        try:
            model = model_cls.model_validate(raw)
        except ValidationError as e:
            logger.warning("The plugin %r has invalid settings, using the defaults:\n%s", identifier, e)
            model = model_cls()

        container.plugins[identifier] = model
        return model


def _assign(model: BaseModel, updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        *parents, field = key.split(".")

        target: Any = model
        for part in parents:
            target = getattr(target, part)

        if not isinstance(target, BaseModel):
            raise TypeError(f"Cannot set {key!r}: {'.'.join(parents)!r} is {target!r}")

        target.__pydantic_validator__.validate_assignment(target, field, _unwrap(value))


def _unwrap(value: Any) -> Any:
    return value._model if isinstance(value, _SettingsProxy) else value


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
            cls.global_settings_model = getattr(cls, "global_settings_model", None)
            cls.local_settings_model = getattr(cls, "local_settings_model", None)

        return cls


class _GraphicsViewProxy(QObject):
    def __init__(self, cursor_reset: _ViewportCursorReset, view: GraphicsView) -> None:
        super().__init__()
        self.__cursor_reset = cursor_reset
        self.__view = view


class _ViewportProxy(QObject):
    def __init__(self, cursor_reset: _ViewportCursorReset, viewport: QWidget) -> None:
        super().__init__()
        self.__cursor_reset = cursor_reset
        self.__viewport = viewport


class _TimelineProxy(QObject):
    def __init__(self, timeline: Timeline) -> None:
        super().__init__()
        self.__timeline = timeline


class _PlaybackProxy(QObject):
    def __init__(self, playback_manager: PlaybackManager) -> None:
        super().__init__()
        self.__playback_manager = playback_manager


class _ViewportCursorReset(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._viewport: ref[QWidget] | None = None

    def track(self, viewport: QWidget) -> None:
        self._viewport = ref(viewport)

    @Slot()
    def reset(self) -> None:
        if self._viewport is None:
            return

        viewport, self._viewport = self._viewport(), None

        if viewport is not None and Shiboken.isValid(viewport):
            viewport.setCursor(Qt.CursorShape.OpenHandCursor)


class _PluginSecrets:
    def __init__(self, plugin: _PluginLike) -> None:
        self.__namespace = plugin.identifier

    def __getattr__(self, name: str) -> Any:
        if name in dir(SecretsManager) and not name.startswith("_"):
            return lambda *args, **kwargs: getattr(SecretsManager, name)(self.__namespace, *args, **kwargs)

        raise AttributeError
