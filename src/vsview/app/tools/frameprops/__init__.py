from typing import Any

from vsview.api import PluginBase
from vsview.api import hookimpl as _vsview_hookimpl

from .plugin import FramePropsPlugin


@_vsview_hookimpl(tryfirst=True)
def vsview_register_toolpanel() -> type[PluginBase[Any, Any]]:
    return FramePropsPlugin
