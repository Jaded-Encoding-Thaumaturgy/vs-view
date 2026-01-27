"""
Plugin hook specifications for vsview.

This module defines the interfaces that plugins should implement to extend the application's functionality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    from vapoursynth import AudioNode, VideoNode

    from .api import NodeProcessor, WidgetPluginBase


hookspec = pluggy.HookspecMarker("vsview")
"""Marker to be used for hook specifications."""

hookimpl = pluggy.HookimplMarker("vsview")
"""Marker to be used for hook implementations."""


# UI Hooks
@hookspec
def vsview_register_tooldock() -> type[WidgetPluginBase[Any, Any]]:
    """
    Register a tool dock widget.

    Returns:
        A WidgetPluginBase subclass defining a QDockWidget-based tool.
    """
    raise NotImplementedError


@hookspec
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    """
    Register a tool panel widget.

    Returns:
        A WidgetPluginBase subclass defining a panel-based tool.
    """
    raise NotImplementedError


@hookspec(firstresult=True)
def vsview_get_video_processor() -> type[NodeProcessor[VideoNode]]:
    """
    Retrieve a processor for the video streams.

    Returns:
        A NodeProcessor[VideoNode] subclass.
        The first registered plugin to return an object takes precedence.
    """
    raise NotImplementedError


@hookspec(firstresult=True)
def vsview_get_audio_processor() -> type[NodeProcessor[AudioNode]]:
    """
    Retrieve a processor for the audio streams.

    This hook allows for real-time processing of the raw audio stream before it reaches the output device.

    Note:
        The input is the untouched audio node from the source.
        If downmixing to stereo is required, it is performed automatically on the node returned by this hook.

    Returns:
        A NodeProcessor[AudioNode] subclass.
        The first registered plugin to return an object takes precedence.
    """
    raise NotImplementedError
