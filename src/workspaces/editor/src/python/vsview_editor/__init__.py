import logging
import os
from typing import Any

from vsview.api import hookimpl
from vsview.api.workspace import PluginBaseWorkspace

from .workspace import EditorWorkspace


@hookimpl
def vsview_register_workspace() -> type[PluginBaseWorkspace[Any, Any]]:
    return EditorWorkspace
