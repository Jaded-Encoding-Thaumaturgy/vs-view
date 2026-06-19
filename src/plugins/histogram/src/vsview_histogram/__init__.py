from typing import Any

from vsview.api import WidgetPluginBase, hookimpl

from .plugin import HistogramPlugin


@hookimpl
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    return HistogramPlugin


@hookimpl
def vsview_register_tooldock() -> type[WidgetPluginBase[Any, Any]]:
    return HistogramPlugin
