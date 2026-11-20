from collections.abc import Callable, Sequence
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
from .callbacks import CallbackHandle, CallbackRegistry
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

    @property
    def loop(self) -> QtEventLoop:
        """Return the global event loop."""
        if not isinstance(loop := get_loop(), QtEventLoop):
            raise CustomTypeError("The current running loop isn't QtEventLoop")
        return loop

    @property
    def env(self) -> ManagedEnvironment:
        """
        Return the managed VapourSynth environment of this workspace.
        Only `prepare_environment` creates it.

        Raises:
            NoEnvironmentError: The workspace holds no environment.
                For a loader workspace: nothing is loaded, the last load failed,
                a reload is between two environments, or the workspace is closing.
        """
        if (env := self._env) is None or env.disposed:
            raise NoEnvironmentError(f"{type(self).__name__} holds no VapourSynth environment")
        return env

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

    def prepare_environment(self) -> ManagedEnvironment:
        """
        Return the environment of this workspace, and create it when the workspace holds none.

        A loader workspace calls it before `loader`.

        Raises:
            NoEnvironmentError: The workspace is closing.
        """
        if self.lifecycle.closing:
            raise NoEnvironmentError(f"{type(self).__name__} is closing and creates no VapourSynth environment")

        if (env := self._env) is None or env.disposed:
            self._env = env = self.new_environment()

        return env

    def register_on_destroy[**P](self, cb: Callable[P, Any], *args: P.args, **kwargs: P.kwargs) -> CallbackHandle:
        """
        Register a callback to run once, when this workspace closes.

        Registering the same callable with the same arguments again is ignored,
        unless an argument cannot be hashed.
        The callbacks run on the GUI thread, in LIFO order, once the workspace has dropped its environment..
        """
        return self._destroy_callbacks.add(cb, *args, **kwargs)

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
        Lifecycle hook called last in the teardown, once the workspace has dropped its environment..

        Subclasses must call `super()`.
        """

    def on_connected(self) -> None:
        """Lifecycle hook called when this workspace is activated and brought into focus."""

    def on_disconnected(self) -> None:
        """Lifecycle hook called when this workspace is deactivated or removed from focus."""

    def confirm_close(self) -> bool:
        """Confirm if workspace can be closed. Returns True to proceed, False to cancel."""
        return True

    def release_environment(self, *, strict: bool = True) -> None:
        """
        Release what this workspace built from its environment, then dispose the environment.

        The environment is disposed before this returns. Call it only when no task of the workspace still runs.
        """
        self._release_content()
        fut = self._dispose_environment()

        if strict and not fut.done():
            raise RuntimeError("Environment isn't disposed yet")

    @final
    def teardown(self) -> UnifiedFuture[None]:
        """
        Release everything this workspace holds: its tasks, its environment and its destroy callbacks.

        It runs once, and `deleteLater` calls it too.
        Subclasses override `on_teardown_started` and `on_teardown_finished` instead.

        Returns:
            A future that is done once the environment is disposed.
            When tasks of this workspace still run, the environment is disposed after they end,
            on a background thread. Otherwise, the future is already done.
        """
        if self.lifecycle.begin_close() is None:
            return UnifiedFuture.resolve(None)

        logger.debug("%s teardown started", type(self).__name__)

        self.on_teardown_started()
        still_running = self._finish_tasks()
        self._release_content()
        disposal = self._dispose_environment(after=still_running)
        self._destroy_callbacks.run_callbacks(self, take_all=True, reverse=True)

        for child in self.findChildren(QObject):
            if isinstance(child, Disposable):
                child.dispose()

        self.on_teardown_finished()
        self.lifecycle.mark_closed()

        return disposal

    def _finish_tasks(self) -> list[Future[Any]]:
        """Stop the workspace's work, and return the tasks that are still running afterwards."""
        return []

    def _release_content(self) -> None:
        """
        Release what this workspace built from its environment, right before the environment is disposed.

        Subclasses must call `super()`.
        """

    def _dispose_environment(self, after: Sequence[Future[Any]] = ()) -> UnifiedFuture[None]:
        """
        Dispose the environment, or only once every task in `after` has ended.

        Returns:
            A future that is done once the environment is disposed.
            It is already done when the environment was disposed at once or there was none.
        """
        if (target := self._take_environment()) is None:
            return UnifiedFuture.resolve(None)

        def dispose() -> None:
            wait(after)
            self._before_disposal(target.environment if isinstance(target, Script) else target)
            clear_environment(target)

        if not after:
            dispose()
            return UnifiedFuture.resolve(None)

        # Disposing a script waits on the lock its still-running body holds, so it must not block the GUI thread.
        logger.debug("Disposing the environment after %d running task(s) end", len(after))
        return self.loop.to_thread_named("ReleaseEnvironment", dispose)

    def _take_environment(self) -> ManagedEnvironment | Script[ManagedEnvironment] | None:
        env, self._env = self._env, None
        return env

    def _before_disposal(self, env: ManagedEnvironment) -> None:
        """Run on the background thread, after the remaining tasks ended and right before `env` is disposed."""
