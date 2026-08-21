from typing import TYPE_CHECKING, Any

__all__ = [
    "catch_output",
    "get_cached",
    "get_reload_count",
    "get_state",
    "is_preview",
    "is_reload",
    "register_on_workspace_destroy",
    "set_output",
]

if TYPE_CHECKING:
    from .api import (
        catch_output,
        get_cached,
        get_reload_count,
        get_state,
        is_preview,
        is_reload,
        register_on_workspace_destroy,
        set_output,
    )
else:
    # The main module is the entry point, so it has to be loaded first before anything else
    # to avoid any circular import
    import vsview.main

    def __getattr__(name: str) -> Any:
        from importlib import import_module

        if name in __all__ and (attr := getattr(import_module("vsview.api"), name, None)):
            return attr

        raise AttributeError(f"Cannot import {name!r} from 'vsview'")
