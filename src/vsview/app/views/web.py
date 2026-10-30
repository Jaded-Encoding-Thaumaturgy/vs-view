import json

from PySide6 import QtWebEngineCore
from PySide6.QtCore import QFile, QIODevice, QUrl

from ...env import getenv_bool


class WebEngineScript(QtWebEngineCore.QWebEngineScript):
    # ruff: ignore[N803]
    def __init__(
        self,
        /,
        *,
        name: str | None = None,
        sourceUrl: QUrl | str | None = None,
        sourceCode: str | None = None,
        injectionPoint: QtWebEngineCore.QWebEngineScript.InjectionPoint | None = None,
        worldId: int | None = None,
        runsOnSubFrames: bool | None = None,
    ) -> None:
        super().__init__()

        # Default QWebEngineScript init doesn't work
        if name is not None:
            self.setName(name)
        if sourceUrl is not None:
            self.setSourceUrl(sourceUrl)
        if sourceCode is not None:
            self.setSourceCode(sourceCode)
        if injectionPoint is not None:
            self.setInjectionPoint(injectionPoint)
        if worldId is not None:
            self.setWorldId(worldId)
        if runsOnSubFrames is not None:
            self.setRunsOnSubFrames(runsOnSubFrames)


class DefaultWebChannelScript(WebEngineScript):
    def __init__(self) -> None:
        f = QFile(":/qtwebchannel/qwebchannel.js")
        f.open(QIODevice.OpenModeFlag.ReadOnly)

        try:
            source = bytes(f.readAll().data()).decode("utf-8")
        finally:
            f.close()

        super().__init__(
            name="qwebchannel",
            sourceCode=source,
            injectionPoint=WebEngineScript.InjectionPoint.DocumentCreation,
            worldId=WebEngineScript.ScriptWorldId.MainWorld,
            runsOnSubFrames=False,
        )


class VSViewEnInjectionScript(WebEngineScript):
    def __init__(self) -> None:
        env_data = {"VSVIEW_DEBUG": getenv_bool("VSVIEW_DEBUG"), "VSVIEW": True}
        super().__init__(
            name="vsviewEnvInjection",
            sourceCode=f"window.ENV = Object.freeze({json.dumps(env_data)});\n",
            injectionPoint=WebEngineScript.InjectionPoint.DocumentCreation,
            worldId=WebEngineScript.ScriptWorldId.MainWorld,
        )
