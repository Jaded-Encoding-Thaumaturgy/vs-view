from collections.abc import Callable
from concurrent.futures import Future, wait
from logging import getLogger
from typing import Any, ClassVar, final, override

from jetpytools import CustomTypeError
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFrame, QMainWindow, QVBoxLayout, QWidget
from vapoursynth import AudioNode, VideoOutputTuple
from vsengine import UnifiedFuture
from vsengine.loops import get_loop
from vsengine.policy import ManagedEnvironment
from vsengine.vpy import Script

from ...assets import IconName
from ...vsenv import QtEventLoop, clear_environment, create_environment
from ..plugins.exceptions import NoEnvironmentError
from ..settings import SettingsManager
from ..settings.models import GlobalSettings
from ..utils import Disposable
from .callbacks import CallbackRegistry
from .lifecycle import WorkspaceLifecycle

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

        self.lifecycle = WorkspaceLifecycle()
        self._env: ManagedEnvironment | None = None
        self._destroy_callbacks = CallbackRegistry()
        self._disposal_waits_for = list[Future[Any]]()
        self._disposal: UnifiedFuture[None] | None = None

    @property
    def loop(self) -> QtEventLoop:
        """Return the global event loop."""
        if not isinstance(loop := get_loop(), QtEventLoop):
            raise CustomTypeError("The current running loop isn't QtEventLoop")
        return loop

    @property
    def env(self) -> ManagedEnvironment:
        """
        Return the managed VapourSynth environment associated with this workspace.

        A workspace that does not load content creates its environment on first use.

        Raises:
            NoEnvironmentError: The workspace is closing.
        """
        if self.lifecycle.closing:
            raise NoEnvironmentError(f"{type(self).__name__} is closing and holds no VapourSynth environment")

        if not self._env or self._env.disposed:
            self._env = self.new_environment()

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

    @override
    def deleteLater(self) -> None:
        self.teardown()
        super().deleteLater()

    def new_environment(self) -> ManagedEnvironment:
        """Create a new VapourSynth environment for this workspace. Subclasses and tests may override it."""
        return create_environment()

    def register_on_destroy[**P, R](self, cb: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Callable[P, R]:
        """
        Register a callback to run once, when this workspace closes.

        Registering the same callable with the same arguments again is ignored,
        unless an argument cannot be hashed.
        The callbacks run in reverse registration order, after the environment is released.
        """
        self._destroy_callbacks.add(cb, *args, **kwargs)
        return cb

    def unregister_on_destroy[**P, R](self, cb: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> bool:
        """
        Unregister a callback registered with `register_on_destroy` and the same arguments.

        Returns:
            True when the callback was registered or False when an argument cannot be hashed.
        """
        return self._destroy_callbacks.remove(cb, *args, **kwargs)

    def on_teardown_started(self) -> None:
        """
        Lifecycle hook called first in the teardown, before playback stops and the workspace's tasks end.

        Stop timers and save state here. Subclasses must call `super()`.
        """

    def on_teardown_finished(self) -> None:
        """
        Lifecycle hook called last in the teardown, after the environment is released.

        Subclasses must call `super()`.
        """

    def on_connected(self) -> None:
        """Lifecycle hook called when this workspace is activated and brought into focus."""

    def on_disconnected(self) -> None:
        """Lifecycle hook called when this workspace is deactivated or removed from focus."""

    def confirm_close(self) -> bool:
        """Confirm if workspace can be closed. Returns True to proceed, False to cancel."""
        return True

    def clear_environment(self) -> None:
        self._dispose_environment()

    @final
    def teardown(self) -> UnifiedFuture[None] | None:
        """
        Release everything this workspace holds: its tasks, its environment and its destroy callbacks.

        It runs once, and `deleteLater` calls it too.
        Subclasses override `on_teardown_started` and `on_teardown_finished` instead.

        Returns:
            When tasks of this workspace still run, the environment is disposed after they end,
            on a background thread, and the returned future is done once it is disposed. None otherwise.
        """
        if self.lifecycle.begin_close() is None:
            return None

        logger.debug("%s teardown started", type(self).__name__)

        self.on_teardown_started()
        self._release_environment(self._finish_tasks())
        self._destroy_callbacks.run_callbacks(self, take_all=True, reverse=True)

        for child in self.findChildren(QObject):
            if isinstance(child, Disposable):
                child.dispose()

        self.on_teardown_finished()
        self.lifecycle.mark_closed()

        disposal, self._disposal = self._disposal, None
        return disposal

    def _finish_tasks(self) -> list[Future[Any]]:
        """Stop the workspace's work, and return the tasks that are still running afterwards."""
        return []

    def _before_delayed_disposal(self, env: ManagedEnvironment) -> None:
        """Run on the background thread, after the remaining tasks ended and right before `env` is disposed."""

    def _release_environment(self, still_running: list[Future[Any]]) -> None:
        self._disposal_waits_for = still_running
        try:
            self.clear_environment()
        finally:
            self._disposal_waits_for = []

    def _take_environment(self) -> ManagedEnvironment | Script[ManagedEnvironment] | None:
        env, self._env = self._env, None
        return env

    def _dispose_environment(self) -> None:
        if (target := self._take_environment()) is None:
            return

        if not (still_running := self._disposal_waits_for):
            clear_environment(target)
            return

        # Disposing a script waits on the lock its still-running body holds, so it must not block the GUI thread.
        def dispose_when_done() -> None:
            wait(still_running)
            self._before_delayed_disposal(target.environment if isinstance(target, Script) else target)
            clear_environment(target)

        logger.debug("Disposing the environment after %d running task(s) end", len(still_running))
        self._disposal = self.loop.to_thread_named("ReleaseEnvironment", dispose_when_done)
