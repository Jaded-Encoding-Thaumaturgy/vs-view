from __future__ import annotations

from vsengine.loops import Cancelled

__all__ = [
    "BlockerRefusedError",
    "NoCurrentVideoOutputError",
    "NoCurrentViewError",
    "NoEnvironmentError",
    "PluginError",
]


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


class NoEnvironmentError(PluginError, RuntimeError):
    """
    Raised when the VapourSynth environment of a workspace is accessed, but the workspace holds none.
    """


class BlockerRefusedError(Cancelled):
    """
    Raised when the workspace cannot be held:
    a load or reload is replacing its environment, or the workspace is closing.

    It is a cancellation, so a background task that raises it ends quietly.
    """
