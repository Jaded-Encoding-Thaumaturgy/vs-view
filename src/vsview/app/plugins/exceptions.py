from __future__ import annotations

__all__ = ["NoCurrentVideoOutputError", "NoCurrentViewError", "PluginError"]


class PluginError(Exception):
    """Base class for plugin-related exceptions."""


class NoCurrentVideoOutputError(PluginError, RuntimeError):
    """
    Raised when `api.current_voutput` or `api.current_time` is accessed
    but no video output is currently available.
    """


class NoCurrentViewError(PluginError, RuntimeError):
    """
    Raised when `api.current_view` is accessed but no graphics view is currently available.
    """
