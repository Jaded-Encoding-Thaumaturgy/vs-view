from __future__ import annotations

import logging
import os
from typing import Any

from vsview.api import WidgetPluginBase, get_console_level, hookimpl

# Suppress internal Chromium C++ stderr output (LOG_FATAL = 3)
name = "QTWEBENGINE_CHROMIUM_FLAGS"
if get_console_level() > logging.DEBUG:
    os.environ[name] = " ".join([os.environ.setdefault(name, ""), "--log-level=3"]).strip()

if "WSL_DISTRO_NAME" in os.environ:
    os.environ[name] = f"{os.environ.get(name, '')} --disable-gpu".strip()

from .plugin import GraphPlugin  # noqa: E402


@hookimpl
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    return GraphPlugin
