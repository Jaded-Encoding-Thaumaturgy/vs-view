from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from logging import getLogger
from typing import ClassVar, override

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QWidget
from vsremote import (
    RemoteAuthenticationError,
    RemoteClient,
    RemoteError,
    RemoteExecutionError,
    RemotePermissionError,
    TransportError,
    UnsupportedFormatError,
    VSRemoteError,
)

from vsview.api import ActionDefinition, IconName
from vsview.api.workspace import PluginWorkspace
from vsview.app.error import StackFrame, show_error
from vsview.app.outputs import VideoMetadata
from vsview.app.workspace import PythonScriptWorkspace

from ._metadata import AUTH_CONTEXT, AUTH_KEY, CURVE_CONTEXT, CURVE_KEY, WORKSPACE_ID
from ._secrets import SessionSecrets
from .dialog import ConnectionConfig, RemoteConnectionDialog
from .exceptions import ConnectionNotConfiguredError
from .settings import GlobalSettings

logger = getLogger(__name__)


class RemoteWorkspace(PythonScriptWorkspace, PluginWorkspace[GlobalSettings, None]):
    title = "Remote"
    icon = IconName.MONITOR_UP
    caption = "Open VapourSynth Script (Remote)"

    content_type = "script"

    identifier = WORKSPACE_ID
    display_name = "Remote"

    CONFIGURE_CONNECTION: ClassVar[ActionDefinition] = ActionDefinition(
        "jet_vsview_remote.configure_connection",
        "Configure Remote Connection...",
        default_key="Ctrl+Alt+R",
    )

    shortcuts = (CONFIGURE_CONNECTION,)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client: RemoteClient | None = None
        self._config: ConnectionConfig | None = None
        self._output_metadata = dict[int, VideoMetadata]()
        self._session_secrets = SessionSecrets(self.secrets, self._on_secrets_error)
        self.dialog = RemoteConnectionDialog(self, self.settings, self._session_secrets)

        self.api.register_shortcut(self.CONFIGURE_CONNECTION, self._on_configure_connection, self)

    @property
    def auth_token(self) -> str | None:
        return self._session_secrets.get(AUTH_CONTEXT, AUTH_KEY)

    @auth_token.setter
    def auth_token(self, value: str | None) -> None:
        self._session_secrets.set(AUTH_CONTEXT, AUTH_KEY, value)

    @property
    def curve_secret_key(self) -> str | None:
        return self._session_secrets.get(CURVE_CONTEXT, CURVE_KEY)

    @curve_secret_key.setter
    def curve_secret_key(self, value: str | None) -> None:
        self._session_secrets.set(CURVE_CONTEXT, CURVE_KEY, value)

    @property
    def client(self) -> RemoteClient:
        if self._config is None:
            raise RuntimeError("This shouldn't happen")

        if self._client and not self._client.is_running:
            logger.warning("Remote client transport is not running; resetting client.")
            self._client.close()
            self._client = None

        if self._client:
            return self._client

        logger.info("Connecting to vsremote server at %s...", self._config.address)
        curve_server_key, curve_public_key, curve_secret_key = self._config.active_curve_keys
        self._client = RemoteClient(
            self._config.address,
            compression=self._config.compression,
            auth_token=self._config.auth_token,
            curve_server_key=curve_server_key,
            curve_public_key=curve_public_key,
            curve_secret_key=curve_secret_key,
            forward_logs=self._config.forward_logs,
            subscribe_streams=self._config.subscribe_streams,
            replay_history=False,
            hwm=self._config.hwm if self._config.hwm is not None else 64,
            tcp_keepalive=self._config.tcp_keepalive,
        )
        return self._client.start()

    @override
    def deleteLater(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        super().deleteLater()

    @override
    def clear_environment(self) -> None:
        self._output_metadata.clear()
        super().clear_environment()

    @override
    def get_output_metadata(self) -> dict[int, VideoMetadata]:
        return self._output_metadata

    @override
    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        try:
            if (config := self._config) is None:
                raise ConnectionNotConfiguredError("Remote connection not configured")

            code = self.content.read_text(encoding="utf-8")
            output_items = self.client.load_code(code, filename=str(self.content)).result(
                timeout=self.settings.global_.load_timeout
            )
            logger.info("Remote script execution completed successfully (%d outputs)", len(output_items))

            for idx, clip in self.client.get_outputs(
                config.prefetch,
                config.backlog,
                frame_timeout=config.frame_timeout,
            ).items():
                clip.set_output(idx)

            self._output_metadata = {item.index: VideoMetadata(item.name) for item in output_items if item.name}
        except (RemoteExecutionError, UnsupportedFormatError) as err:
            self.statusLoadingErrored.emit("Script error")
            show_error(
                _strip_local_paths(err, str(self.content)) if isinstance(err, RemoteError) else err,
                self,
                user_script_path=str(self.content),
                header_suffix=" on the remote server",
            )
            err.__traceback__ = None
            raise RuntimeError(err) from None
        except (ConnectionNotConfiguredError, UnicodeDecodeError, VSRemoteError, TimeoutError) as err:
            logger.debug("Full traceback:", exc_info=True)
            self.statusLoadingErrored.emit(_failure_status(err))

            err.__traceback__ = None
            show_error(
                _strip_local_paths(err, str(self.content)) if isinstance(err, RemoteError) else err,
                self,
                user_script_path=str(self.content),
            )
            raise err from None
        except Exception as err:
            logger.exception("Remote Error:")
            err.__traceback__ = None
            raise err from None

    def prompt_connection_dialog(self) -> ConnectionConfig | None:
        if self.dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        res = self.dialog.config
        # Persist non-sensitive configuration to global plugin settings
        self.settings.global_.address = res.address
        self.settings.global_.compression = res.compression
        self.settings.global_.prefetch = res.prefetch
        self.settings.global_.backlog = res.backlog if res.backlog is not None else max(res.prefetch * 3, 12)
        self.settings.global_.frame_timeout = res.frame_timeout
        self.settings.global_.hwm = res.hwm
        self.settings.global_.tcp_keepalive = res.tcp_keepalive
        self.settings.global_.forward_logs = res.forward_logs
        self.settings.global_.subscribe_streams = res.subscribe_streams
        self.settings.global_.use_curve = res.use_curve
        self.settings.global_.curve_server_key = res.curve_server_key
        self.settings.global_.curve_public_key = res.curve_public_key
        # Persist sensitive secrets
        self.auth_token = res.auth_token
        self.curve_secret_key = res.curve_secret_key

        return res

    def _on_secrets_error(self, message: str) -> None:
        self.api.statusMessage.emit(message)
        self.dialog.set_status(message, "#e74c3c")

    def _apply_config(self, config: ConnectionConfig) -> None:
        if config != self._config and self._client is not None:
            self._client.close()
            self._client = None

        self._config = config

    @Slot()
    @override
    def _on_open_file_button_clicked(self) -> None:
        if (config := self.prompt_connection_dialog()) is None:
            return

        self._apply_config(config)
        super()._on_open_file_button_clicked()

    @Slot()
    def _on_configure_connection(self) -> None:
        if (config := self.prompt_connection_dialog()) is None:
            return

        self._apply_config(config)
        if not getattr(self, "content", None):
            return

        if self.stack.currentWidget() is self.error_page:
            self.load_content(self.content)
        else:
            self.reload_content()


@dataclass
class RemoteStackFrame:
    filename: str
    lineno: int | None
    func_name: str | None = None
    code: str | None = None


@dataclass
class RemoteErrorView:
    exc_type: str
    exc_msg: str
    filename: str | None
    lineno: int | None
    code_line: str | None = None
    formatted_traceback: str | None = None
    frames: Sequence[StackFrame] | None = None


def _strip_local_paths(err: RemoteError, script_path: str) -> RemoteErrorView:
    def rename(filename: str) -> str:
        return filename if filename == script_path else f"<remote:{filename}>"

    return RemoteErrorView(
        exc_type=err.exc_type,
        exc_msg=err.exc_msg,
        filename=rename(err.filename) if err.filename is not None else None,
        lineno=err.lineno,
        code_line=err.code_line,
        formatted_traceback=err.formatted_traceback,
        frames=[RemoteStackFrame(rename(f.filename), f.lineno, f.func_name, f.code) for f in err.frames],
    )


def _failure_status(err: BaseException) -> str:
    match err:
        case ConnectionNotConfiguredError():
            return "Remote connection not configured"
        case UnicodeDecodeError():
            return "Script is not valid UTF-8"
        case RemoteAuthenticationError():
            return "Authentication failed"
        case RemotePermissionError():
            return "Permission denied by the server"
        case TimeoutError():
            return "Remote server timed out"
        case TransportError():
            return "Connection error"
        case _:
            return "Remote error"
