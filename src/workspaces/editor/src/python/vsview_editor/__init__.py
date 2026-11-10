import logging
import os
from typing import Any

from vsview.api import get_console_level, hookimpl
from vsview.api.workspace import PluginBaseWorkspace

name = "QTWEBENGINE_CHROMIUM_FLAGS"
if get_console_level() > logging.DEBUG:
    os.environ[name] = " ".join([os.environ.setdefault(name, ""), "--log-level=3"]).strip()

if "WSL_DISTRO_NAME" in os.environ:
    os.environ[name] = f"{os.environ.get(name, '')} --disable-gpu".strip()

from .workspace import EditorWorkspace  # noqa: E402


@hookimpl
def vsview_register_workspace() -> type[PluginBaseWorkspace[Any, Any]]:
    return EditorWorkspace
