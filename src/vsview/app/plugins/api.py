"""
Plugin API for VSView.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Self, TypeVar

import vapoursynth as vs
from pydantic import BaseModel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QShowEvent
from PySide6.QtWidgets import QWidget

from vsview.app.views.video import BaseGraphicsView
from vsview.vsenv.loop import run_in_loop

from ._interface import _PluginAPI, _PluginBaseMeta


@dataclass(frozen=True, slots=True)
class VideoOutputProxy:
    """Read-only proxy for a video output."""

    vs_index: int
    """Index of the video output in the VapourSynth environment."""

    vs_name: str | None
    """Name of the video output, if any, when using `vsview.set_output()`."""

    vs_output: vs.VideoOutputTuple
    """The object created by `vapoursynth.get_outputs()`."""

    props: Mapping[int, Mapping[str, Any]]
    """
    Frame properties of the clip.
    """


class PluginAPI(_PluginAPI):
    """API for plugins to interact with the workspace."""

    if TYPE_CHECKING:
        statusMessage = Signal(str)  # message
        """Signal to emit status messages."""

        globalSettingsChanged = Signal()
        """Signal to emit when global settings change."""

        localSettingsChanged = Signal(str)
        """Signal to emit when local settings change."""

    @property
    def file_path(self) -> Path | None:
        """Return the file path of the currently loaded file, or None if not a file."""
        return self._settings_store.file_path

    @property
    def current_frame(self) -> int:
        """Return the current frame number."""
        return self.__workspace.current_frame

    @property
    def current_tab_index(self) -> int:
        """Return the index of the currently selected tab."""
        return self.__workspace.current_tab_index

    @property
    def voutputs(self) -> dict[int, VideoOutputProxy]:
        """Return a dictionary of VideoOutputProxy objects for all tabs."""
        return {
            k: VideoOutputProxy(voutput.vs_index, voutput.vs_name, voutput.vs_output, voutput.props)
            for k, voutput in self.__workspace.tab_manager.voutputs.items()
        }

    if TYPE_CHECKING:

        @property
        def current_voutput(self) -> VideoOutputProxy:
            """Return the VideoOutput for the currently selected tab."""
            ...

    @property
    def is_playing(self) -> bool:
        """Return whether playback is currently active."""
        return self.__workspace.tbar.is_playing

    def register_on_destroy(self, cb: Callable[[], Any]) -> None:
        """
        Register a callback to be called before the workspace begins a reload or when the workspace is destroyed.
        This is generaly used to clean up VapourSynth resources.
        """
        self.__workspace.cbs_on_destroy.append(cb)

    @contextmanager
    def vs_context(self) -> Iterator[None]:
        """
        Context manager for using the VapourSynth environment of the workspace.
        """
        with self.__workspace.env.use():
            yield

    def frame_to_pixmap(
        self,
        f: vs.VideoFrame,
        flags: Qt.ImageConversionFlag = Qt.ImageConversionFlag.NoFormatConversion,
    ) -> QPixmap:
        """
        Convert a VapourSynth frame to a QPixmap. Assume the frame is already packed in GRAY32 format.
        """
        return QPixmap.fromImage(self.__workspace._packer.frame_to_qimage(f), flags).copy()


class LocalSettingsModel(BaseModel):
    """
    Base class for settings with optional local overrides.

    Fields set to `None` fall back to the corresponding global value.
    """

    def resolve(self, global_settings: BaseModel) -> Self:
        """
        Resolve global settings with local overrides applied.

        Args:
            global_settings: Source of default values.

        Returns:
            A new instance with all fields resolved.
        """
        base_values = global_settings.model_dump(include=set(self.__class__.model_fields))

        overrides = self.model_dump(exclude_none=True)

        return self.__class__(**base_values | overrides)


if sys.version_info >= (3, 13):
    TGlobalSettings = TypeVar("TGlobalSettings", bound=BaseModel | None, default=None)
    TLocalSettings = TypeVar("TLocalSettings", bound=BaseModel | None, default=None)
else:
    import typing_extensions

    TGlobalSettings = typing_extensions.TypeVar("TGlobalSettings", bound=BaseModel | None, default=None)
    TLocalSettings = typing_extensions.TypeVar("TLocalSettings", bound=BaseModel | None, default=None)


class PluginSettings(Generic[TGlobalSettings, TLocalSettings]):
    """
    Settings wrapper providing lazy, always-fresh access.

    Returns None if no settings model is defined for the scope.
    """

    def __init__(self, plugin: PluginBase[TGlobalSettings, TLocalSettings]) -> None:
        self._plugin = plugin

    @property
    def global_(self) -> TGlobalSettings:
        """Get the current global settings."""
        return self._plugin.api._get_cached_settings(self._plugin, "global")

    @property
    def local_(self) -> TLocalSettings:
        """Get the current local settings (resolved with global fallbacks)."""
        return self._plugin.api._get_cached_settings(self._plugin, "local")


class PluginBase(QWidget, Generic[TGlobalSettings, TLocalSettings], metaclass=_PluginBaseMeta):  # noqa: UP046
    """Base class for all plugins."""

    identifier: ClassVar[str]
    """Unique identifier for the tool."""

    display_name: ClassVar[str]
    """Display name for the tool."""

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.api._init_plugin(self)

    @property
    def settings(self) -> PluginSettings[TGlobalSettings, TLocalSettings]:
        """Get the settings wrapper for lazy, always-fresh access."""
        return PluginSettings(self)

    def update_global_settings(self, **updates: Any) -> None:
        """Update specific global settings fields and trigger persistence."""
        self.api._update_settings(self, "global", **updates)

    def update_local_settings(self, **updates: Any) -> None:
        """Update specific local settings fields and trigger persistence."""
        self.api._update_settings(self, "local", **updates)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the current video output changes.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """
        self.on_current_frame_changed(self.api.current_frame)

    def on_current_frame_changed(self, n: int) -> None:
        """
        Called when the current frame changes.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """

    def on_playback_started(self) -> None:
        """
        Called when playback starts.

        Execution Thread: **Main**.
        """

    def on_playback_stopped(self) -> None:
        """
        Called when playback stops.

        Execution Thread: **Main**.
        """


class PluginGraphicsView(BaseGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api

        self.outputs = dict[int, vs.VideoNode]()
        self.current_tab = -1

        self.api.register_on_destroy(self.outputs.clear)

    @run_in_loop(return_future=False)
    def update_display(self, image: QPixmap) -> None:
        """Update the UI with the new image on the main thread."""
        self.pixmap_item.setPixmap(image)

    def refresh(self) -> None:
        """Refresh the view."""
        self.api._init_view(self, refresh=True)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        """
        Called when the current video output changes.

        **Warning**: Do not call `self.refresh()` here, as it will cause an infinite loop.
        If you need to update the display manually, use `self.update_display()`.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """

    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        """
        Called when the current frame changes.
        `n` is the frame number and `f` is the packed VideoFrame in GRAY32 format.

        **Warning**: Do not call `self.refresh()` here, as it will cause an infinite loop.
        If you need to update the display manually, use `self.update_display()`.

        Execution Thread: **Main or Background**.
        If you need to update the UI, use the `@run_in_loop` decorator.
        """
        self.update_display(self.api.frame_to_pixmap(f))

    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        """
        Override this to transform the clip before it is displayed.
        By default, it returns the clip as-is.
        """
        return clip
