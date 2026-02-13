from typing import Any

from vsview.api import WidgetPluginBase, hookimpl

from .plugin import SceningPlugin


@hookimpl
def vsview_register_tooldock() -> type[WidgetPluginBase[Any, Any]]:
    return SceningPlugin
