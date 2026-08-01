from vsview.api import hookimpl
from vsview.app.workspace import BaseWorkspace

from .workspace import EditorWorkspace


@hookimpl
def vsview_register_workspace() -> type[BaseWorkspace]:
    return EditorWorkspace
