import logging
import os
from typing import Any

from vsview.api import get_console_level, hookimpl
from vsview.api.workspace import PluginBaseWorkspace

# Suppress internal Chromium C++ stderr output (LOG_FATAL = 3)
if get_console_level() > logging.DEBUG:
    name = "QTWEBENGINE_CHROMIUM_FLAGS"
    os.environ[name] = " ".join([os.environ.setdefault(name, ""), "--log-level=3"]).strip()


from .workspace import EditorWorkspace


@hookimpl
def vsview_register_workspace() -> type[PluginBaseWorkspace[Any, Any]]:
    return EditorWorkspace
