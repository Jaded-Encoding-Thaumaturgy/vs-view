from __future__ import annotations

from typing import Any

from vsview.api import WidgetPluginBase, hookimpl

from .plugin import GraphPlugin


@hookimpl
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    return GraphPlugin
