from collections.abc import Callable
from functools import partial
from logging import getLogger
from typing import Any, ClassVar, override

from jetpytools import CustomTypeError
from PySide6.QtWidgets import QFrame, QMainWindow, QVBoxLayout, QWidget
from vapoursynth import AudioNode, VideoOutputTuple
from vsengine.loops import get_loop
from vsengine.policy import ManagedEnvironment

from ...assets import IconName
from ...vsenv import QtEventLoop, clear_environment, create_environment
from ..settings import SettingsManager
from ..settings.models import GlobalSettings

logger = getLogger(__name__)


class BaseWorkspace(QMainWindow):
    """Base class for all workspaces."""

    title: ClassVar[str]
    """The display title for this workspace type."""

    icon: ClassVar[IconName]
    """The icon for this workspace type."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._central_widget = QFrame(self)
        self._central_widget.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self.setCentralWidget(self._central_widget)

        self.current_layout = QVBoxLayout(self._central_widget)
        self.current_layout.setContentsMargins(0, 0, 0, 0)

        self._env: ManagedEnvironment | None = None
        self._on_destroy_callbacks = dict[
            tuple[Callable[..., Any], tuple[Any, ...], tuple[tuple[str, Any], ...]],
            Callable[[], Any],
        ]()

    @property
    def loop(self) -> QtEventLoop:
        """Return the global event loop."""
        if not isinstance(loop := get_loop(), QtEventLoop):
            raise CustomTypeError("The current running loop isn't QtEventLoop")
        return loop

    @property
    def env(self) -> ManagedEnvironment:
        """Return the managed VapourSynth environment associated with this workspace."""
        if not self._env or (self._env and self._env.disposed):
            self._env = create_environment()

        return self._env

    @property
    def outputs(self) -> dict[int, VideoOutputTuple | AudioNode]:
        """Return a copy of all outputs in the environment."""
        return self.env.outputs.copy()

    @property
    def video_outputs(self) -> dict[int, VideoOutputTuple]:
        """Return a dictionary of video outputs."""
        return {k: v for k, v in self.env.outputs.items() if isinstance(v, VideoOutputTuple)}

    @property
    def audio_outputs(self) -> dict[int, AudioNode]:
        """Return a dictionary of audio outputs."""
        return {k: v for k, v in self.env.outputs.items() if isinstance(v, AudioNode)}

    @property
    def global_settings(self) -> GlobalSettings:
        """Return the global settings for this workspace."""
        return SettingsManager.global_settings

    def register_on_destroy[**P, R](self, cb: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Callable[P, R]:
        """Register a callback to run when this workspace is destroyed."""
        key = cb, args, tuple(kwargs.items())
        pcb = partial(cb, *args, **kwargs)

        if key not in self._on_destroy_callbacks:
            self._on_destroy_callbacks[key] = pcb

        return cb

    def unregister_on_destroy[**P, R](self, cb: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> None:
        """Unregister a previously registered on_destroy callback."""
        key = cb, args, tuple(kwargs.items())
        self._on_destroy_callbacks.pop(key, None)

    @override
    def deleteLater(self) -> None:
        self._run_on_destroy_callbacks()
        self.clear_environment()
        return super().deleteLater()

    def on_connected(self) -> None:
        """Lifecycle hook called when this workspace is activated and brought into focus."""

    def on_disconnected(self) -> None:
        """Lifecycle hook called when this workspace is deactivated or removed from focus."""

    def confirm_close(self) -> bool:
        """Confirm if workspace can be closed. Returns True to proceed, False to cancel."""
        return True

    def clear_environment(self) -> None:
        self._dispose_environment()

    def _dispose_environment(self) -> None:
        if self._env:
            clear_environment(self._env)
            self._env = None

    def _run_on_destroy_callbacks(self) -> None:
        logger.debug("Running on_destroy callbacks")
        for callback in reversed(self._on_destroy_callbacks.values()):
            try:
                callback()
            except Exception:
                logger.exception("Error running on_destroy callback %r on workspace %r", callback, self)
        self._on_destroy_callbacks.clear()
