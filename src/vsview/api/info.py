from __future__ import annotations

import sys
from collections.abc import Callable, MutableMapping
from functools import partial
from typing import Any, NamedTuple, Self


def is_preview() -> bool:
    """Check if the current script is running in a preview environment (VSView only)."""
    return bool(sys.modules.get("__vsview__"))


class Context(NamedTuple):
    """Internal context injected into scripts running inside VSView."""

    reload_count: int
    is_reload: bool
    persistent_state: MutableMapping[Any, Any]
    on_workspace_destroy: Callable[[Callable[[], Any]], Any]

    @classmethod
    def get(cls) -> Self | None:
        """Get the VSView context, or None if outside VSView."""
        return getattr(mod, "__vsview_context__", None) if (mod := sys.modules.get("__vsview__")) is not None else None


def register_on_workspace_destroy[**P, R](cb: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Callable[P, R]:
    """
    Register a callback to run when the workspace is destroyed.

    The callback is returned to facilitate usage as a decorator.

    Args:
        callback: A callable to invoke upon workspace destruction.
        *args: Positional arguments forwarded to callback.
        **kwargs: Keyword arguments forwarded to callback.

    Returns:
        The registered callback.
    """
    if (ctx := Context.get()) is not None:
        ctx.on_workspace_destroy(partial(cb, *args, **kwargs))
    return cb


def is_reload() -> bool | None:
    """
    Check if the current script run is a reload inside VSView.
    """
    return ctx.is_reload if (ctx := Context.get()) is not None else None


def get_reload_count() -> int | None:
    """
    Return how many times the current script has been reloaded in VSView
    (0 if initial load or None outside preview).
    """
    return ctx.reload_count if (ctx := Context.get()) is not None else None


def get_state() -> MutableMapping[Any, Any] | None:
    """
    Return the persistent state dictionary shared across script reloads, or None if outside VSView.

    Note:
        Objects stored in this dictionary survive script reloads within the same workspace.
        - DO NOT store VapourSynth clips, frames, or script closures capturing 'globals()'.
        - DO store heavy external resources (e.g. PyTorch models, ONNX sessions, lookup tables).
    """
    return ctx.persistent_state if (ctx := Context.get()) is not None else None


def get_cached[**P, R](key: str, loader: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """
    Get a cached value from the persistent state, or compute and cache it if not present.

    If running outside VSView, the loader is called directly without caching.

    Args:
        key: The key to look up or store in the persistent state.
        loader: A callable to produce the value if key is not found.
        *args: Positional arguments forwarded to loader.
        **kwargs: Keyword arguments forwarded to loader.

    Returns:
        The cached or newly computed value.
    """
    if (state := get_state()) is None:
        return loader(*args, **kwargs)
    if key not in state:
        state[key] = loader(*args, **kwargs)
    return state[key]
