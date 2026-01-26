from typing import ClassVar, cast

from PySide6.QtWidgets import QFrame, QMainWindow, QVBoxLayout, QWidget
from vapoursynth import AudioNode, VideoOutputTuple
from vsengine.loops import get_loop
from vsengine.policy import ManagedEnvironment

from ...assets import IconName
from ...vsenv import QtEventLoop, clear_environment, create_environment
from ..settings import SettingsManager
from ..settings.models import GlobalSettings


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

    @property
    def loop(self) -> QtEventLoop:
        """Return the global event loop."""
        return cast(QtEventLoop, get_loop())

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

    def deleteLater(self) -> None:
        self.clear_environment()
        return super().deleteLater()

    def clear_environment(self) -> None:
        if env := getattr(self, "script", self._env):
            clear_environment(env)
