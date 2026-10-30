"""API for vsview"""

from typing import TYPE_CHECKING, Any

from ..app.icon import IconName, IconReloadMixin, load_icon
from ..app.packing import Packer
from ..app.plugins import (
    AudioOutputProxy,
    GraphicsViewProxy,
    LocalSettingsModel,
    NoCurrentVideoOutputError,
    NoCurrentViewError,
    NodeProcessor,
    PlaybackProxy,
    PluginAPI,
    PluginError,
    PluginGraphicsView,
    PluginSecrets,
    PluginSettings,
    TimelineProxy,
    VideoOutputProxy,
    WidgetPluginBase,
    WorkspaceBlocker,
    hookimpl,
)
from ..app.settings.action import ActionDefinition
from ..app.settings.metadata import (
    Checkbox,
    ColorPicker,
    DoubleSpin,
    Dropdown,
    FilePicker,
    LineEdit,
    ListEdit,
    Login,
    PathListEdit,
    PlainTextEdit,
    SecretLineEdit,
    Spin,
    WidgetMetadata,
    WidgetTimeEdit,
)
from ..app.settings.widgets import (
    ColorPickerInput,
    CustomSpinBox,
    FilePickerWidget,
    ListEditWidget,
    LoginCredentialsInput,
    PathListEditWidget,
)
from ..app.utils import ObjectType, QObjectCounter, QObjectSet, cache_clip, path_to_hash
from ..app.views import OutputInfo
from ..app.views.components import AbstractTableModel, Accordion, AnimatedToggle, NonClosingMenu, SegmentedControl
from ..app.views.timeline import FrameEdit, TimeEdit
from ..app.views.video import BaseGraphicsView
from ..logging import get_console_level, setup_web_logger
from ..types import Frame, Time
from ..vsenv import run_in_background, run_in_loop
from .info import Context, get_cached, get_reload_count, get_state, is_preview, is_reload, register_on_workspace_destroy
from .output import catch_output, get_output, get_outputs, set_output

if TYPE_CHECKING:
    from ..app.views.web import DefaultWebChannelScript, VSViewEnInjectionScript, WebEngineScript

__all__ = [
    "AbstractTableModel",
    "Accordion",
    "ActionDefinition",
    "AnimatedToggle",
    "AudioOutputProxy",
    "BaseGraphicsView",
    "Checkbox",
    "ColorPicker",
    "ColorPickerInput",
    "Context",
    "CustomSpinBox",
    "DefaultWebChannelScript",
    "DoubleSpin",
    "Dropdown",
    "FilePicker",
    "FilePickerWidget",
    "Frame",
    "FrameEdit",
    "GraphicsViewProxy",
    "IconName",
    "IconReloadMixin",
    "LineEdit",
    "ListEdit",
    "ListEditWidget",
    "LocalSettingsModel",
    "Login",
    "LoginCredentialsInput",
    "NoCurrentVideoOutputError",
    "NoCurrentViewError",
    "NodeProcessor",
    "NonClosingMenu",
    "ObjectType",
    "OutputInfo",
    "Packer",
    "PathListEdit",
    "PathListEditWidget",
    "PlainTextEdit",
    "PlaybackProxy",
    "PluginAPI",
    "PluginError",
    "PluginGraphicsView",
    "PluginSecrets",
    "PluginSettings",
    "QObjectCounter",
    "QObjectSet",
    "SecretLineEdit",
    "SegmentedControl",
    "Spin",
    "Time",
    "TimeEdit",
    "TimelineProxy",
    "VSViewEnInjectionScript",
    "VideoOutputProxy",
    "WebEngineScript",
    "WidgetMetadata",
    "WidgetPluginBase",
    "WidgetTimeEdit",
    "WorkspaceBlocker",
    "cache_clip",
    "catch_output",
    "get_cached",
    "get_console_level",
    "get_output",
    "get_outputs",
    "get_reload_count",
    "get_state",
    "hookimpl",
    "is_preview",
    "is_reload",
    "load_icon",
    "path_to_hash",
    "register_on_workspace_destroy",
    "run_in_background",
    "run_in_loop",
    "set_output",
    "setup_web_logger",
]

if not TYPE_CHECKING:

    def __getattr__(name: str) -> Any:
        import importlib

        if name in ["DefaultWebChannelScript", "VSViewEnInjectionScript", "WebEngineScript"]:
            mod = importlib.import_module("vsview.app.views.web")
            return getattr(mod, name)

        raise AttributeError
