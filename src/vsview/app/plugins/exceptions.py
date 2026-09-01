from __future__ import annotations

__all__ = ["NoCurrentVideoOutputError", "PluginError"]


class PluginError(Exception):
    """Base class for plugin-related exceptions."""


class NoCurrentVideoOutputError(PluginError, RuntimeError):
    """
    Raised when `api.current_voutput` or `api.current_time` is accessed
    but no video output is currently available.
    """
