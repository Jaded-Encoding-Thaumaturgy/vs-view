from __future__ import annotations

from logging import getLogger
from typing import ClassVar, override

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QWidget
from vsremote import RemoteClient, RemoteExecutionError, UnsupportedFormatError

from vsview.api import ActionDefinition, IconName
from vsview.api.workspace import PluginWorkspace
from vsview.app.error import show_error
from vsview.app.outputs import VideoMetadata
from vsview.app.workspace import PythonScriptWorkspace

from ._metadata import AUTH_CONTEXT, AUTH_KEY, CURVE_CONTEXT, CURVE_KEY, WORKSPACE_ID
from .dialog import ConnectionConfig, RemoteConnectionDialog
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

        self.dialog = RemoteConnectionDialog(self, self.settings, self.secrets)

        self.api.register_shortcut(self.CONFIGURE_CONNECTION, self._on_configure_connection, self)

    @override
    def deleteLater(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        super().deleteLater()

    @property
    def auth_token(self) -> str | None:
        return self.secrets.get(AUTH_CONTEXT, AUTH_KEY)

    @auth_token.setter
    def auth_token(self, value: str | None) -> None:
        if value:
            self.secrets.set(AUTH_CONTEXT, AUTH_KEY, value)
        else:
            self.secrets.delete(AUTH_CONTEXT, AUTH_KEY)

    @property
    def curve_secret_key(self) -> str | None:
        return self.secrets.get(CURVE_CONTEXT, CURVE_KEY)

    @curve_secret_key.setter
    def curve_secret_key(self, value: str | None) -> None:
        if value:
            self.secrets.set(CURVE_CONTEXT, CURVE_KEY, value)
        else:
            self.secrets.delete(CURVE_CONTEXT, CURVE_KEY)

    @property
    def client(self) -> RemoteClient:
        if self._config is None:
            raise RuntimeError("This shouldn't happen")

        if self._client:
            return self._client

        logger.info("Connecting to vsremote server at %s...", self._config.address)
        self._client = RemoteClient(
            self._config.address,
            compression=self._config.compression,
            auth_token=self._config.auth_token,
            curve_server_key=self._config.curve_server_key,
            curve_public_key=self._config.curve_public_key,
            curve_secret_key=self._config.curve_secret_key,
            forward_logs=self._config.forward_logs,
            subscribe_streams=self._config.subscribe_streams,
            replay_history=False,
        )
        return self._client.start()

    @override
    def get_output_metadata(self) -> dict[int, VideoMetadata]:
        return self._output_metadata

    @override
    def loader(self) -> None:
        if not self.content.exists():
            logger.error("File not found: %s", self.content)
            raise FileNotFoundError(f"File not found: {self.content}")

        if self._config is None:
            raise RuntimeError("Remote connection not configured")

        code = self.content.read_text(encoding="utf-8")
        try:
            output_items = self.client.load_code(code, filename=str(self.content)).result(timeout=30.0)
            logger.info("Remote script execution completed successfully (%d outputs)", len(output_items))

            for idx, clip in self.client.get_outputs(self._config.prefetch, self._config.backlog).items():
                clip.set_output(idx)

            self._output_metadata = {item.index: VideoMetadata(item.name) for item in output_items if item.name}
        except (RemoteExecutionError, UnsupportedFormatError) as err:
            self.statusLoadingErrored.emit("Script error")
            show_error(err, self, user_script_path=str(self.content), header_suffix=" on the remote server")
            err.__traceback__ = None
            raise RuntimeError(err) from None
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
        self.settings.global_.forward_logs = res.forward_logs
        self.settings.global_.subscribe_streams = res.subscribe_streams
        self.settings.global_.use_curve = res.use_curve
        self.settings.global_.curve_server_key = res.curve_server_key
        self.settings.global_.curve_public_key = res.curve_public_key
        # Persist sensitive secrets
        self.auth_token = res.auth_token
        self.curve_secret_key = res.curve_secret_key

        return res

    @Slot()
    @override
    def _on_open_file_button_clicked(self) -> None:
        self._config = self.prompt_connection_dialog()
        if self._config is None:
            return

        super()._on_open_file_button_clicked()

    @Slot()
    def _on_configure_connection(self) -> None:
        if self.prompt_connection_dialog() and hasattr(self, "content") and self.content:
            self.reload_content()
