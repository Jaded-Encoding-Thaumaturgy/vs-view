from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any, Literal, NamedTuple, cast, override

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from vsengine import UnifiedFuture
from vsremote import RemoteAuthenticationError, RemoteClient, RemoteTimeoutError, TransportError, VSRemoteError
from vsremote.protocol import validate_curve_key

from vsview.api import CustomSpinBox, PluginSettings, run_in_background

from ._metadata import AUTH_CONTEXT, AUTH_KEY, CURVE_CONTEXT, CURVE_KEY
from ._secrets import SessionSecrets
from .settings import GlobalSettings

logger = logging.getLogger(__name__)


class RemoteConnectionDialog(QDialog):
    def __init__(self, parent: QWidget, settings: PluginSettings[GlobalSettings], secrets: SessionSecrets) -> None:
        super().__init__(parent)

        self.setWindowTitle("VSRemote Connection")
        self.setMinimumWidth(500)
        self.setModal(True)

        self.settings = settings
        self.secrets = secrets
        self._ping_worker: UnifiedFuture[None] | None = None
        self._insecure_address_confirmed: str | None = None

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        main_layout.addWidget(QLabel("Configure connection details", self, wordWrap=True))

        # Connection parameters form
        form_frame = QFrame(self, frameShape=QFrame.Shape.StyledPanel)
        form_layout = QFormLayout(form_frame)
        form_layout.setContentsMargins(10, 10, 10, 10)
        form_layout.setSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.address_edit = QLineEdit(form_frame)
        self.token_edit = QLineEdit(
            form_frame,
            echoMode=QLineEdit.EchoMode.PasswordEchoOnEdit,
            placeholderText="Optional authentication token",
        )

        self.compression_combo = QComboBox(form_frame)
        self.compression_combo.addItems(["zstd", "none"])

        self.prefetch_spin = QSpinBox(form_frame, minimum=0, maximum=64)
        self.prefetch_spin.setToolTip("Number of frames to prefetch ahead of time (0 to disable)")

        self.backlog_spin = CustomSpinBox(form_frame, min_text="Default")
        self.backlog_spin.setRange(0, 128)
        self.backlog_spin.setToolTip("Maximum buffer of in-flight and prefetched frames")

        self.frame_timeout_spin = CustomSpinBox(form_frame, min_text="Default", custom_suffix=" s")
        self.frame_timeout_spin.setRange(0, 120)
        self.frame_timeout_spin.setToolTip(
            "Maximum seconds to wait for a frame before timing out "  # no format
            "(0 or Default to use the client timeout of 30 s)"
        )

        self.forward_logs_cb = QCheckBox(form_frame)
        self.subscribe_streams_cb = QCheckBox(form_frame)

        form_layout.addRow("Host", self.address_edit)
        form_layout.addRow("Auth Token", self.token_edit)
        form_layout.addRow("Compression", self.compression_combo)
        form_layout.addRow("Prefetch Frames", self.prefetch_spin)
        form_layout.addRow("Backlog Buffer", self.backlog_spin)
        form_layout.addRow("Frame Timeout", self.frame_timeout_spin)
        form_layout.addRow("Forward remote server log records", self.forward_logs_cb)
        form_layout.addRow("Subscribe remote stdout / stderr streams", self.subscribe_streams_cb)
        main_layout.addWidget(form_frame)

        # CurveZMQ Encryption Section
        form_frame2 = QFrame(self, frameShape=QFrame.Shape.StyledPanel)
        form_layout2 = QFormLayout(form_frame2)
        form_layout2.setContentsMargins(10, 10, 10, 10)
        form_layout2.setSpacing(8)
        form_layout2.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.use_curve_cb = QCheckBox(form_frame2)
        self.use_curve_cb.toggled.connect(self._on_curve_toggled)

        self.curve_server_key_edit = QLineEdit(form_frame2)
        self.curve_server_key_edit.setPlaceholderText("Server public key (Z85 encoded)")

        self.curve_public_key_edit = QLineEdit(form_frame2)
        self.curve_public_key_edit.setPlaceholderText("Client public key (optional)")

        self.curve_secret_key_edit = QLineEdit(form_frame2, echoMode=QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.curve_secret_key_edit.setPlaceholderText("Client secret key (optional)")

        form_layout2.addRow("Enable CurveZMQ end-to-end encryption", self.use_curve_cb)
        form_layout2.addRow("Server key", self.curve_server_key_edit)
        form_layout2.addRow("Client public key", self.curve_public_key_edit)
        form_layout2.addRow("Client secret key", self.curve_secret_key_edit)
        main_layout.addWidget(form_frame2)

        # Test Connection Section
        test_container = QWidget(self)
        test_layout = QHBoxLayout(test_container)
        test_layout.setContentsMargins(0, 4, 0, 4)
        test_layout.setSpacing(8)

        self.test_btn = QPushButton("Test Connection", test_container)
        self.test_btn.clicked.connect(self._on_test_connection_clicked)
        test_layout.addWidget(self.test_btn)

        self.status_label = QLabel("", test_container)
        self.status_label.setWordWrap(True)
        test_layout.addWidget(self.status_label, stretch=1)
        main_layout.addWidget(test_container)

        # Dialog Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

    def refresh_from_settings(self) -> None:
        sett = self.settings.global_
        self.address_edit.setText(sett.address)
        self.token_edit.setText(self.auth_token or "")
        self.compression_combo.setCurrentText(sett.compression)
        self.prefetch_spin.setValue(sett.prefetch)
        self.backlog_spin.setValue(sett.backlog or 0)
        self.frame_timeout_spin.setValue(int(sett.frame_timeout) if sett.frame_timeout is not None else 0)
        self.forward_logs_cb.setChecked(sett.forward_logs)
        self.subscribe_streams_cb.setChecked(sett.subscribe_streams)
        self.use_curve_cb.setChecked(sett.use_curve)
        self.curve_server_key_edit.setText(sett.curve_server_key or "")
        self.curve_public_key_edit.setText(sett.curve_public_key or "")
        self.curve_secret_key_edit.setText(self.curve_secret_key or "")
        self._on_curve_toggled(sett.use_curve)

    @property
    def config(self) -> ConnectionConfig:
        return ConnectionConfig(
            address=self.address_edit.text().strip(),
            auth_token=self.token_edit.text().strip() or None,
            compression=cast(Literal["zstd", "none"], self.compression_combo.currentText()),
            prefetch=self.prefetch_spin.value(),
            backlog=self.backlog_spin.value() or None,
            forward_logs=self.forward_logs_cb.isChecked(),
            subscribe_streams=self.subscribe_streams_cb.isChecked(),
            use_curve=self.use_curve_cb.isChecked(),
            curve_server_key=self.curve_server_key_edit.text().strip() or None,
            curve_public_key=self.curve_public_key_edit.text().strip() or None,
            curve_secret_key=self.curve_secret_key_edit.text().strip() or None,
            frame_timeout=v if (v := self.frame_timeout_spin.value()) > 0 else None,
            hwm=self.settings.global_.hwm,
            tcp_keepalive=self.settings.global_.tcp_keepalive,
        )

    @property
    def auth_token(self) -> str | None:
        return self.secrets.get(AUTH_CONTEXT, AUTH_KEY)

    @property
    def curve_secret_key(self) -> str | None:
        return self.secrets.get(CURVE_CONTEXT, CURVE_KEY)

    @override
    def accept(self) -> None:
        cfg = self.config

        if (problem := cfg.validate()) is not None:
            self.set_status(problem, "#e74c3c")
            return

        if self._confirm_insecure_connection(cfg):
            super().accept()

    @override
    def exec(self) -> int:
        self._insecure_address_confirmed = None
        self.set_status()
        self.refresh_from_settings()
        res = super().exec()
        if self._ping_worker:
            logger.debug("Cancelling ping task")
            self._ping_worker.cancel()
            self._ping_worker = None
        return res

    def set_status(self, text: str = "", color: str = "", *, bold: bool = False) -> None:
        style = f"color: {color};" if color else "color: palette(text);"
        if bold:
            style += " font-weight: bold;"
        self.status_label.setStyleSheet(style)
        self.status_label.setText(text)

    def _confirm_insecure_connection(self, cfg: ConnectionConfig) -> bool:
        if cfg.active_curve_keys[0] is not None or is_local_address(cfg.address):
            return True
        if cfg.address == self._insecure_address_confirmed:
            return True

        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Unencrypted Connection",
            f"CurveZMQ encryption is off for {cfg.address}. "
            "Traffic, including your auth token, will be sent in plain text.\n\n"
            "Continue anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
            textFormat=Qt.TextFormat.PlainText,
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)

        if box.exec() != QMessageBox.StandardButton.Yes:
            self.set_status("Not connected. Turn on CurveZMQ or use a local address.", "#e74c3c")
            return False

        self._insecure_address_confirmed = cfg.address
        return True

    @Slot(bool)
    def _on_curve_toggled(self, checked: bool) -> None:
        self.curve_server_key_edit.setEnabled(checked)
        self.curve_public_key_edit.setEnabled(checked)
        self.curve_secret_key_edit.setEnabled(checked)

    @Slot()
    def _on_test_connection_clicked(self) -> None:
        cfg = self.config

        if (problem := cfg.validate()) is not None:
            self.set_status(problem, "#e74c3c")
            return

        if not self._confirm_insecure_connection(cfg):
            return

        self.set_status("Connecting...")
        self.test_btn.setDisabled(True)
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setDisabled(True)

        def on_success(_: None) -> None:
            self.set_status("Connected successfully!", "#2ecc71", bold=True)

        def on_error(e: BaseException) -> None:
            match e:
                case RemoteAuthenticationError():
                    self.set_status("Authentication failed: invalid or missing auth token.", "#e74c3c")
                case RemoteTimeoutError() | TimeoutError():
                    self.set_status("Connection timed out.", "#e74c3c")
                case TransportError():
                    self.set_status(f"Transport error: {e}", "#e74c3c")
                case VSRemoteError():
                    self.set_status(f"Remote error: {e}", "#e74c3c")
                case _:
                    self.set_status(f"Connection failed: {e}", "#e74c3c")

        def restore_state(*_: Any) -> None:
            self.test_btn.setEnabled(True)
            self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            self._ping_worker = None

        self._ping_worker = (
            self.check_connection(cfg)
            .then(on_success, on_error, cancel_cb=restore_state, on_loop=True)
            .add_loop_callback(restore_state)
        )

    @run_in_background(name="PingServer")
    def check_connection(self, cfg: ConnectionConfig) -> None:
        curve_server_key, curve_public_key, curve_secret_key = cfg.active_curve_keys
        with RemoteClient(
            cfg.address,
            compression=cfg.compression,
            auth_token=cfg.auth_token,
            curve_server_key=curve_server_key,
            curve_public_key=curve_public_key,
            curve_secret_key=curve_secret_key,
            subscribe_streams=False,
            forward_logs=False,
            hwm=cfg.effective_hwm,
            tcp_keepalive=cfg.tcp_keepalive,
        ) as client:
            client.get_stats().result(timeout=5.0)


class ConnectionConfig(NamedTuple):
    address: str
    auth_token: str | None
    compression: Literal["zstd", "none"]
    prefetch: int
    backlog: int | None
    forward_logs: bool
    subscribe_streams: bool
    use_curve: bool
    curve_server_key: str | None
    curve_public_key: str | None
    curve_secret_key: str | None
    frame_timeout: float | None = None
    hwm: int | None = None
    tcp_keepalive: bool = True

    @property
    def effective_hwm(self) -> int:
        """The ZeroMQ high water mark to connect with: the set value, or twice the frame backlog when it is Auto."""
        if self.hwm is not None:
            return self.hwm
        backlog = self.prefetch * 3 if self.backlog is None else max(self.backlog, self.prefetch)
        return max(backlog * 2, 16)

    @property
    def active_curve_keys(self) -> tuple[str | None, str | None, str | None]:
        """The server, public and secret CurveZMQ keys to connect with: all None when CurveZMQ is disabled."""
        if not self.use_curve:
            return None, None, None
        return self.curve_server_key, self.curve_public_key, self.curve_secret_key

    def validate(self) -> str | None:
        """
        Check the address and the CurveZMQ keys before they are saved.
        Returns the problem, or None.
        """
        scheme, sep, rest = self.address.partition("://")
        if not sep or not scheme:
            return "Enter a ZeroMQ address with a scheme, for example tcp://127.0.0.1:5555."

        if scheme == "tcp":
            # The part before ";" is an optional local source address; only the destination is checked.
            destination = rest.rpartition(";")[2]
            host, colon, port = destination.rpartition(":")
            if not destination or (colon and not host):
                return "The tcp address needs a host, for example tcp://127.0.0.1:5555."
            if not colon or not port.isdigit() or not 1 <= int(port) <= 65535:
                return "The tcp address needs a port from 1 to 65535, for example tcp://127.0.0.1:5555."

        server_key, public_key, secret_key = self.active_curve_keys
        for label, name, key in (
            ("Server key", "curve_server_key", server_key),
            ("Client public key", "curve_public_key", public_key),
            ("Client secret key", "curve_secret_key", secret_key),
        ):
            try:
                validate_curve_key(key, name)
            except ValueError as e:
                return f"{label}: {e}"

        if (public_key is None) != (secret_key is None):
            return "Set both the client public key and the client secret key, or neither."
        if public_key is not None and server_key is None:
            return "The client keys need the server key."

        return None


def is_local_address(address: str) -> bool:
    scheme, sep, rest = address.partition("://")
    if not sep:
        return False
    if scheme in ("ipc", "inproc"):
        return True
    if scheme != "tcp":
        return False

    host = rest.rpartition(";")[2].rpartition(":")[0].removeprefix("[").removesuffix("]")
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
