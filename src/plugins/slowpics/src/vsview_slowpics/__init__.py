from typing import Any

from vsview.api import (
    WidgetPluginBase,
    hookimpl,
)

from .main import SlowPicsPlugin

__version__: str
__version_tuple__: tuple[int | str, ...]

try:
    from ._version import __version__, __version_tuple__
except ImportError:
    __version__ = "0.0.0+unknown"
    __version_tuple__ = (0, 0, 0, "+unknown")

@hookimpl
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    return SlowPicsPlugin
