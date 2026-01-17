"""
Plugin hook specifications for vsview.

This module defines the interfaces that plugins should implement to extend the application's functionality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    from .api import PluginBase

hookspec = pluggy.HookspecMarker("vsview")
"""Marker to be used for hook specifications."""

hookimpl = pluggy.HookimplMarker("vsview")
"""Marker to be used for hook implementations."""


@hookspec
def vsview_register_tooldock() -> type[PluginBase[Any, Any]]:
    """Return a ToolSpec for a tool dock widget."""
    raise NotImplementedError


@hookspec
def vsview_register_toolpanel() -> type[PluginBase[Any, Any]]:
    """Return a ToolSpec for a tool panel widget."""
    raise NotImplementedError
