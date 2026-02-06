from typing import Any

from vsview.api import WidgetPluginBase, hookimpl

from .plugin import ColorPickerPlugin


@hookimpl
def vsview_register_tooldock() -> type[WidgetPluginBase[Any]]:
    return ColorPickerPlugin
