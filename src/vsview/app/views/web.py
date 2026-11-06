from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from enum import StrEnum
from functools import wraps
from logging import DEBUG, ERROR, WARNING, getLogger
from typing import Any, ClassVar, Generic, TypeVar, get_args, get_origin, overload, override

from jetpytools import CustomNotImplementedError, copy_signature
from PySide6 import QtWebEngineCore
from PySide6.QtCore import QEvent, QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QPalette, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMessageBox, QWidget

from ...env import getenv_bool
from ...logging import setup_web_logger
from ..utils import get_all_original_bases

if sys.version_info >= (3, 13):
    FallbackT = TypeVar("FallbackT", default=None)
else:
    import typing_extensions

    FallbackT = typing_extensions.TypeVar("FallbackT", default=None)

logger = getLogger(__name__)


class SafeSlot(Generic[FallbackT]):
    @overload
    def __init__(
        self,
        *types: type | str,
        name: str | None = ...,
        result: type | str | None = ...,
        fallback: None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *types: type | str,
        name: str | None = ...,
        result: type | str | None = ...,
        fallback: FallbackT,
    ) -> None: ...
    def __init__(
        self,
        *types: type | str,
        name: str | None = None,
        result: type | str | None = None,
        fallback: Any = None,
    ) -> None:
        self._types = types
        self._kwargs = dict[str, Any]()
        self._fallback = fallback

        if name is not None:
            self._kwargs["name"] = name
        if result is not None:
            self._kwargs["result"] = result

    def __call__[**P, R](self, func: Callable[P, R]) -> Callable[P, R | FallbackT]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | FallbackT:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.debug("%s failed", func.__name__, stacklevel=2, exc_info=e)
                return self._fallback

        return Slot(*self._types, **self._kwargs)(wrapper)


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


class BaseWebChannelBridge[PyCmdT: StrEnum, JsCmdT: StrEnum](QObject):
    """Base QWebChannel bridge implementing the bidirectional command bus pattern."""

    _js_command_cls: ClassVar[type[Any]]

    dispatchCommandSignal = Signal(str, str)
    webReady = Signal()
    commandReceived = Signal(StrEnum, dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._is_ready = False

    @override
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in get_all_original_bases(cls):
            if get_origin(base) is BaseWebChannelBridge:
                args = get_args(base)
                if len(args) == 2 and isinstance(args[1], type) and issubclass(args[1], StrEnum):
                    cls._js_command_cls = args[1]
                    break

    @property
    def is_ready(self) -> bool:
        """Whether the frontend web view has completed initialization."""
        return self._is_ready

    def dispatch(self, command: PyCmdT, payload: Mapping[str, Any] | None = None) -> None:
        """Dispatch a command payload to the JavaScript frontend."""
        self.dispatchCommandSignal.emit(command, json.dumps(payload) if payload is not None else "")

    def on_command_received(self, command: JsCmdT, payload: dict[str, Any]) -> None:
        """Hook for subclasses to update internal bridge state prior to emitting commandReceived."""

    @Slot()
    def onWebReady(self) -> None:
        """Invoked by JavaScript when the page and QWebChannel are initialized."""
        logger.debug("%s web frontend is ready.", type(self).__name__)
        self._is_ready = True
        self.webReady.emit()

    @Slot(str, str)
    def dispatchFromJs(self, command: str, payload_json: str) -> None:
        """Receive an action or event from the JavaScript frontend and forward via commandReceived."""
        try:
            cmd = self._js_command_cls(command)
            parsed = json.loads(payload_json) if payload_json else {}
            payload = parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.error("Failed to decode JSON from JS dispatch '%s': %s", command, e)
            return

        self.on_command_received(cmd, payload)
        self.commandReceived.emit(cmd, payload)


class BaseWebEnginePage(QWebEnginePage):
    LOG_LEVELS: ClassVar[Mapping[QWebEnginePage.JavaScriptConsoleMessageLevel, int]] = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: DEBUG,
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: WARNING,
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: ERROR,
    }

    def __init__(
        self,
        parent: QObject,
        background_color: QColor | None = None,
        logger_name: str = "vsview_web_js",
    ) -> None:
        super().__init__(parent)
        self._js_logger = setup_web_logger(logger_name)
        if background_color is not None:
            self.setBackgroundColor(background_color)

    @property
    def view(self) -> QWidget:
        if isinstance(view := self.parent(), QWidget):
            return view
        raise CustomNotImplementedError

    @override
    def javaScriptAlert(self, security_origin: QUrl | str, msg: str) -> None:
        QMessageBox.information(self.view, "Alert", msg, QMessageBox.StandardButton.Ok)

    @override
    def javaScriptConfirm(self, security_origin: QUrl | str, msg: str) -> bool:
        result = QMessageBox.question(
            self.view,
            "Confirm",
            msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Ok

    @override
    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        self._js_logger.log(
            self.LOG_LEVELS.get(level, DEBUG),
            message,
            extra={"js_source": source_id, "js_lineno": line_number},
        )


class BaseWebEngineView[BridgeT: BaseWebChannelBridge[Any, Any]](QWebEngineView):
    _bridge_cls: ClassVar[type[BaseWebChannelBridge[Any, Any]]]
    logger_name: ClassVar[str] = "vsview_web_js"

    bridge: BridgeT

    @override
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in get_all_original_bases(cls):
            if get_origin(base) is BaseWebEngineView:
                args = get_args(base)
                if args and isinstance(args[0], type) and issubclass(args[0], BaseWebChannelBridge):
                    cls._bridge_cls = args[0]
                    break

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setPage(
            BaseWebEnginePage(
                self,
                background_color=self.palette().color(QPalette.ColorRole.Window),
                logger_name=self.logger_name,
            )
        )

        self.bridge = self._bridge_cls(self)  # type: ignore[assignment]

        channel = QWebChannel(self, propertyUpdateInterval=-1)
        channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(channel)

        self.page().scripts().insert(DefaultWebChannelScript())
        self.page().scripts().insert(VSViewEnInjectionScript())

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.page().setBackgroundColor(self.palette().color(QPalette.ColorRole.Window))

    @copy_signature(QWebEngineView.load)
    @override
    def load(self, thing: Any, /) -> None:
        logger.debug("Loading %s from %s", type(self).__name__, thing)
        return super().load(thing)

    @override
    def deleteLater(self) -> None:
        self.page().webChannel().deregisterObject(self.bridge)
        self.page().webChannel().deleteLater()
        self.page().deleteLater()
        super().deleteLater()
