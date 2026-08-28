from __future__ import annotations

from typing import Any

from vsview.api import hookimpl
from vsview.api.workspace import PluginBaseWorkspace

from .workspace import RemoteWorkspace


@hookimpl
def vsview_register_workspace() -> type[PluginBaseWorkspace[Any, Any]]:
    return RemoteWorkspace
