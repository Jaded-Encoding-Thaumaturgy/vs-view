from __future__ import annotations

import logging
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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from vsengine import UnifiedFuture
from vsremote import RemoteAuthenticationError, RemoteClient, RemoteTimeoutError, TransportError, VSRemoteError

from vsview.api import CustomSpinBox, PluginSecrets, PluginSettings, run_in_background, run_in_loop

from ._metadata import AUTH_CONTEXT, AUTH_KEY, CURVE_CONTEXT, CURVE_KEY
from .settings import GlobalSettings

logger = logging.getLogger(__name__)


class RemoteConnectionDialog(QDialog):
    def __init__(self, parent: QWidget, settings: PluginSettings[GlobalSettings], secrets: PluginSecrets) -> None:
        super().__init__(parent)

        self.setWindowTitle("VSRemote Connection")
        self.setMinimumWidth(500)
        self.setModal(True)

        self.settings = settings
        self.secrets = secrets
        self._ping_worker: UnifiedFuture[None] | None = None

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

        self.address_edit = QLineEdit(form_frame, text=self.settings.global_.address)
        self.token_edit = QLineEdit(
            form_frame,
            echoMode=QLineEdit.EchoMode.PasswordEchoOnEdit,
            placeholderText="Optional authentication token",
        )
        self.token_edit.setText(self.auth_token or "")

        self.compression_combo = QComboBox(form_frame)
        self.compression_combo.addItems(["zstd", "none"])
        self.compression_combo.setCurrentText(self.settings.global_.compression)

        self.prefetch_spin = QSpinBox(form_frame, minimum=0, maximum=64, value=self.settings.global_.prefetch)
        self.prefetch_spin.setToolTip("Number of frames to prefetch ahead of time (0 to disable)")

        self.backlog_spin = CustomSpinBox(form_frame, min_text="Default")
        self.backlog_spin.setRange(0, 128)
        self.backlog_spin.setValue(self.settings.global_.backlog or 0)
        self.backlog_spin.setToolTip("Maximum buffer of in-flight and prefetched frames")

        self.forward_logs_cb = QCheckBox(form_frame)
        self.forward_logs_cb.setChecked(self.settings.global_.forward_logs)
        self.subscribe_streams_cb = QCheckBox(form_frame)
        self.subscribe_streams_cb.setChecked(self.settings.global_.subscribe_streams)

        form_layout.addRow("Host", self.address_edit)
        form_layout.addRow("Auth Token", self.token_edit)
        form_layout.addRow("Compression", self.compression_combo)
        form_layout.addRow("Prefetch Frames", self.prefetch_spin)
        form_layout.addRow("Backlog Buffer", self.backlog_spin)
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
        self.use_curve_cb.setChecked(
            bool(
                self.settings.global_.curve_server_key
                or self.settings.global_.curve_public_key
                or self.curve_secret_key
            )
        )
        self.use_curve_cb.toggled.connect(self._on_curve_toggled)

        self.curve_server_key_edit = QLineEdit(form_frame2, text=self.settings.global_.curve_server_key)
        self.curve_server_key_edit.setPlaceholderText("Server public key (Z85 encoded)")

        self.curve_public_key_edit = QLineEdit(form_frame2, text=self.settings.global_.curve_public_key)
        self.curve_public_key_edit.setPlaceholderText("Client public key (optional)")

        self.curve_secret_key_edit = QLineEdit(form_frame2, echoMode=QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.curve_secret_key_edit.setPlaceholderText("Client secret key (optional)")
        self.curve_secret_key_edit.setText(self.curve_secret_key)
        self._on_curve_toggled(self.use_curve_cb.isChecked())

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

    @property
    @run_in_loop(return_future=False)
    def config(self) -> ConnectionConfig:
        return ConnectionConfig(
            address=self.address_edit.text().strip(),
            auth_token=self.token_edit.text().strip() or None,
            compression=cast(Literal["zstd", "none"], self.compression_combo.currentText()),
            prefetch=self.prefetch_spin.value(),
            backlog=self.backlog_spin.value() or None,
            forward_logs=self.forward_logs_cb.isChecked(),
            subscribe_streams=self.subscribe_streams_cb.isChecked(),
            use_curve=(use_curve := self.use_curve_cb.isChecked()),
            curve_server_key=self.curve_server_key_edit.text().strip() or None if use_curve else None,
            curve_public_key=self.curve_public_key_edit.text().strip() or None if use_curve else None,
            curve_secret_key=self.curve_secret_key_edit.text().strip() or None if use_curve else None,
        )

    @property
    def auth_token(self) -> str | None:
        return self.secrets.get(AUTH_CONTEXT, AUTH_KEY)

    @property
    def curve_secret_key(self) -> str | None:
        return self.secrets.get(CURVE_CONTEXT, CURVE_KEY)

    @override
    def exec(self) -> int:
        self.set_status()
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

    @Slot(bool)
    def _on_curve_toggled(self, checked: bool) -> None:
        self.curve_server_key_edit.setEnabled(checked)
        self.curve_public_key_edit.setEnabled(checked)
        self.curve_secret_key_edit.setEnabled(checked)

    @Slot()
    def _on_test_connection_clicked(self) -> None:
        self.set_status("Connecting...")
        self.test_btn.setDisabled(True)
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setDisabled(True)

        def on_success(result: bool) -> None:
            if result:
                self.set_status("Connected successfully!", "#2ecc71", bold=True)
            else:
                self.set_status("Server did not respond to ping.", "#e74c3c")

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
            self.ping()
            .then(on_success, on_error, cancel_cb=restore_state, on_loop=True)
            .add_loop_callback(restore_state)
        )

    @run_in_background(name="PingServer")
    def ping(self) -> bool:
        cfg = self.config

        with RemoteClient(
            cfg.address,
            compression=cfg.compression,
            auth_token=cfg.auth_token,
            curve_server_key=cfg.curve_server_key,
            curve_public_key=cfg.curve_public_key,
            curve_secret_key=cfg.curve_secret_key,
            subscribe_streams=False,
            forward_logs=False,
        ) as client:
            return client.ping().result(timeout=5.0)


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
