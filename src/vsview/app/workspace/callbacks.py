from __future__ import annotations

from collections.abc import Callable, Hashable
from functools import partial
from logging import getLogger
from threading import Lock
from typing import Any

from PySide6.QtCore import QObject
from vsengine.loops import Cancelled

logger = getLogger(__name__)


class CallbackHandle:
    """Removes one registered callback."""

    __slots__ = ("_remove",)

    def __init__(self, remove: Callable[[], bool]) -> None:
        self._remove = remove

    def remove(self) -> bool:
        """
        Remove the callback.

        Returns:
            True when the callback was still registered.
            False when it was already removed or has already run.
        """
        return self._remove()


class CallbackRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._callbacks = dict[Hashable, Callable[[], Any]]()

    def add[**P](self, cb: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs) -> CallbackHandle:
        key = _make_key(cb, args, kwargs)
        remove = partial(self._discard, key)

        with self._lock:
            if key in self._callbacks:
                return CallbackHandle(remove)
            self._callbacks[key] = partial(cb, *args, **kwargs) if args or kwargs else cb

        if isinstance(owner := getattr(cb, "__self__", None), QObject):
            owner.destroyed.connect(remove)

        return CallbackHandle(remove)

    def remove[**P](self, cb: Callable[P, Any], /, *args: P.args, **kwargs: P.kwargs) -> bool:
        return self._discard(_make_key(cb, args, kwargs))

    def snapshot(self) -> list[Callable[[], Any]]:
        with self._lock:
            return list(self._callbacks.values())

    def take_all(self) -> list[Callable[[], Any]]:
        with self._lock:
            callbacks = list(self._callbacks.values())
            self._callbacks.clear()
        return callbacks

    def _discard(self, key: Hashable) -> bool:
        with self._lock:
            return self._callbacks.pop(key, None) is not None


def run_callbacks(callbacks: list[Callable[[], Any]], owner: object) -> None:
    for cb in callbacks:
        try:
            cb()
        except Cancelled as e:
            logger.debug("Callback %r of %r was cancelled: %s", cb, owner, e)
        except Exception:
            logger.exception("Error running callback %r of %r", cb, owner)


def _make_key(cb: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
    key = (cb, args, tuple(kwargs.items()))
    try:
        hash(key)
    except TypeError:
        return object()
    return key
