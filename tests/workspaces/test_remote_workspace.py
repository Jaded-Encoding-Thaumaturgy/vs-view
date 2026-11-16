from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import keyring.errors
import msgspec
import pytest
import zmq
from keyring.backends.fail import Keyring
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFileDialog
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment
from vsremote import RemoteTimeoutError, TransportNotConnectedError
from vsremote.protocol import Command, StatusCode
from vsremote.protocol.messages import RemoteErrorPayload, StackFrame
from vsview_remote._secrets import READ_ERROR_MESSAGE, WRITE_ERROR_MESSAGE
from vsview_remote.dialog import ConnectionConfig
from vsview_remote.workspace import RemoteWorkspace

from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.secrets import SecretsManager
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.qt, pytest.mark.integration, pytest.mark.vpy("no-core")]

SERVER_KEY = "S" * 40
PUBLIC_KEY = "P" * 40
SECRET_KEY = "c" * 40


type Reply = tuple[StatusCode, bytes] | None
type Handler = Callable[[Command, bytes, str | None], Reply]


class FakeServer:
    """A ROUTER socket on 127.0.0.1 that answers vsremote requests with whatever the handler returns."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler = handler
        self.requests = list[tuple[Command, str | None]]()
        self.address = ""
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="RemoteFakeServer", daemon=True)
        self._thread.start()
        assert self._ready.wait(5)

    def tokens(self, cmd: Command) -> list[str | None]:
        return [t for c, t in self.requests if c == cmd]

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def _run(self) -> None:
        ctx = zmq.Context()
        sock = ctx.socket(zmq.ROUTER)
        sock.setsockopt(zmq.LINGER, 0)
        port = sock.bind_to_random_port("tcp://127.0.0.1")
        self.address = f"tcp://127.0.0.1:{port}"
        self._ready.set()

        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        try:
            while not self._stop.is_set():
                if not dict(poller.poll(20)):
                    continue
                identity, req_id, cmd_byte, *rest = sock.recv_multipart()
                cmd = Command(cmd_byte[0])
                payload = rest[0] if rest else b""
                token = rest[1].decode() if len(rest) > 1 else None
                self.requests.append((cmd, token))

                reply = self.handler(cmd, payload, token) if self.handler is not None else None
                if reply is None and cmd in (Command.SUBSCRIBE_STREAM, Command.UNSUBSCRIBE_STREAM):
                    reply = StatusCode.OK, b"OK"
                if reply is not None:
                    sock.send_multipart([identity, req_id, bytes([reply[0]]), reply[1]])
        finally:
            sock.close(linger=0)
            ctx.term()


def error_reply(payload: RemoteErrorPayload, status: StatusCode = StatusCode.ERROR) -> Handler:
    def handler(cmd: Command, _: bytes, __: str | None) -> Reply:
        return (status, msgspec.msgpack.encode(payload)) if cmd == Command.LOAD_CODE else None

    return handler


SCRIPT_ERROR = RemoteErrorPayload("ValueError: boom", "ValueError", "boom", "<remote>", 2, "raise ValueError('boom')")


@pytest.fixture(autouse=True)
def _setup(qt_event_loop: QtEventLoop, init_plugin_manager: PluginManager) -> None:
    return None


@pytest.fixture
def servers() -> Generator[Callable[[Handler | None], FakeServer]]:
    made = list[FakeServer]()

    def factory(handler: Handler | None = None) -> FakeServer:
        made.append(server := FakeServer(handler))
        return server

    try:
        yield factory
    finally:
        for s in made:
            s.close()


@pytest.fixture
def dialogs(mocker: MockerFixture) -> list[str]:
    shown = list[str]()

    def fake_dialog(parent: Any, message: str, title: str = "Error") -> int:
        shown.append(message)
        return 0

    mocker.patch("vsview.app.error.display_error_dialog", side_effect=fake_dialog)
    return shown


@pytest.fixture
def workspace(qtbot: QtBot) -> RemoteWorkspace:
    # qtbot calls deleteLater at teardown, which closes any live client.
    ws = RemoteWorkspace()
    qtbot.addWidget(ws)
    # Keeps the fake server simple: no log forwarding, no stream subscription, no compression.
    ws.settings.global_.forward_logs = False
    ws.settings.global_.subscribe_streams = False
    ws.settings.global_.compression = "none"
    return ws


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "remote_script.vpy"
    path.write_text("import vapoursynth as vs\nraise ValueError('boom')\n", encoding="utf-8")
    return path


def accept_dialog(ws: RemoteWorkspace, before_accept: Callable[[], Any] | None = None) -> ConnectionConfig | None:
    # The dialog is modal: a zero timer edits the widgets and presses Connect from inside exec().
    def press_connect() -> None:
        if before_accept is not None:
            before_accept()
        ws.dialog.accept()

    QTimer.singleShot(0, press_connect)
    return ws.prompt_connection_dialog()


def connect_through_load_button(
    ws: RemoteWorkspace,
    script: Path,
    mocker: MockerFixture,
    before_accept: Callable[[], Any] | None = None,
    *,
    accept: bool = True,
) -> MagicMock:
    mocker.patch.object(QFileDialog, "getOpenFileName", return_value=(str(script), ""))
    load_content = mocker.patch.object(ws, "load_content")

    def close_dialog() -> None:
        if before_accept is not None:
            before_accept()
        if accept:
            ws.dialog.accept()
        else:
            ws.dialog.reject()

    QTimer.singleShot(0, close_dialog)
    ws.load_btn.click()
    return load_content


def enter_server(ws: RemoteWorkspace, server: FakeServer, token: str | None = None) -> Callable[[], None]:
    def edit() -> None:
        ws.dialog.address_edit.setText(server.address)
        ws.dialog.token_edit.setText(token or "")

    return edit


def store_curve_keys(ws: RemoteWorkspace, *, use_curve: bool) -> None:
    ws.settings.global_.use_curve = use_curve
    ws.settings.global_.curve_server_key = SERVER_KEY
    ws.settings.global_.curve_public_key = PUBLIC_KEY
    ws.curve_secret_key = SECRET_KEY


def test_connect_with_curve_unticked_keeps_the_stored_keys(workspace: RemoteWorkspace) -> None:
    store_curve_keys(workspace, use_curve=True)
    workspace.auth_token = "token"

    res = accept_dialog(workspace, lambda: workspace.dialog.use_curve_cb.setChecked(False))

    assert res is not None
    assert res.use_curve is False
    assert (res.curve_server_key, res.curve_public_key, res.curve_secret_key) == (SERVER_KEY, PUBLIC_KEY, SECRET_KEY)
    assert workspace.settings.global_.use_curve is False
    assert workspace.settings.global_.curve_server_key == SERVER_KEY
    assert workspace.settings.global_.curve_public_key == PUBLIC_KEY
    assert workspace.curve_secret_key == SECRET_KEY
    assert workspace.auth_token == "token"


@pytest.mark.parametrize("use_curve", [True, False])
def test_dialog_curve_checkbox_follows_the_use_curve_setting(workspace: RemoteWorkspace, use_curve: bool) -> None:
    store_curve_keys(workspace, use_curve=use_curve)
    shown = list[tuple[bool, bool, str, str, str]]()

    def record() -> None:
        dialog = workspace.dialog
        shown.append(
            (
                dialog.use_curve_cb.isChecked(),
                dialog.curve_server_key_edit.isEnabled(),
                dialog.curve_server_key_edit.text(),
                dialog.curve_public_key_edit.text(),
                dialog.curve_secret_key_edit.text(),
            )
        )

    res = accept_dialog(workspace, record)

    assert shown == [(use_curve, use_curve, SERVER_KEY, PUBLIC_KEY, SECRET_KEY)]
    assert res is not None
    assert res.use_curve is use_curve
    assert workspace.settings.global_.use_curve is use_curve


@pytest.mark.parametrize(
    ("use_curve", "expected_keys"),
    [(True, (SERVER_KEY, PUBLIC_KEY, SECRET_KEY)), (False, (None, None, None))],
)
def test_client_uses_the_curve_keys_only_when_use_curve_is_set(
    workspace: RemoteWorkspace,
    script: Path,
    mocker: MockerFixture,
    use_curve: bool,
    expected_keys: tuple[str | None, str | None, str | None],
) -> None:
    store_curve_keys(workspace, use_curve=use_curve)
    remote_client = mocker.patch("vsview_remote.workspace.RemoteClient")
    connect_through_load_button(workspace, script, mocker)

    assert workspace.client is remote_client.return_value.start.return_value

    kwargs = remote_client.call_args.kwargs
    assert (kwargs["curve_server_key"], kwargs["curve_public_key"], kwargs["curve_secret_key"]) == expected_keys


@pytest.mark.parametrize(
    ("use_curve", "expected_keys"),
    [(True, (SERVER_KEY, PUBLIC_KEY, SECRET_KEY)), (False, (None, None, None))],
)
def test_ping_uses_the_curve_keys_only_when_use_curve_is_set(
    workspace: RemoteWorkspace,
    mocker: MockerFixture,
    use_curve: bool,
    expected_keys: tuple[str | None, str | None, str | None],
) -> None:
    store_curve_keys(workspace, use_curve=use_curve)
    remote_client = mocker.patch("vsview_remote.dialog.RemoteClient")
    remote_client.return_value.__enter__.return_value.ping.return_value.result.return_value = True
    res = accept_dialog(workspace)
    assert res is not None

    assert workspace.dialog.ping(res).result(timeout=5) is True

    kwargs = remote_client.call_args.kwargs
    assert (kwargs["curve_server_key"], kwargs["curve_public_key"], kwargs["curve_secret_key"]) == expected_keys


def test_load_button_with_new_settings_connects_to_the_new_server(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
) -> None:
    server_a = servers(error_reply(SCRIPT_ERROR))
    server_b = servers(error_reply(SCRIPT_ERROR))
    connect_through_load_button(workspace, script, mocker, enter_server(workspace, server_a, "old-token"))
    workspace.content = script
    with pytest.raises(RuntimeError, match="boom"):
        workspace.loader()

    load_content = connect_through_load_button(
        workspace, script, mocker, enter_server(workspace, server_b, "new-token")
    )
    with pytest.raises(RuntimeError, match="boom"):
        workspace.loader()

    load_content.assert_called_once_with(script)
    assert server_a.tokens(Command.LOAD_CODE) == ["old-token"]
    assert server_b.tokens(Command.LOAD_CODE) == ["new-token"]
    assert len(dialogs) == 2


def test_cancelling_the_load_dialog_keeps_the_previous_connection(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
) -> None:
    server = servers(error_reply(SCRIPT_ERROR))
    connect_through_load_button(workspace, script, mocker, enter_server(workspace, server, "token"))
    workspace.content = script
    with pytest.raises(RuntimeError, match="boom"):
        workspace.loader()

    load_content = connect_through_load_button(workspace, script, mocker, accept=False)
    with pytest.raises(RuntimeError, match="boom"):
        workspace.loader()

    load_content.assert_not_called()
    assert server.tokens(Command.LOAD_CODE) == ["token", "token"]
    assert len(dialogs) == 2


def test_configure_connection_on_the_error_page_loads_the_script_again(
    workspace: RemoteWorkspace,
    script: Path,
    mocker: MockerFixture,
) -> None:
    workspace.content = script
    workspace.clear_failed_load()
    load_content = mocker.patch.object(workspace, "load_content")
    reload_content = mocker.patch.object(workspace, "reload_content")

    QTimer.singleShot(0, workspace.dialog.accept)
    workspace._on_configure_connection()

    assert workspace.stack.currentWidget() is workspace.error_page
    load_content.assert_called_once_with(script)
    reload_content.assert_not_called()


def test_configure_connection_on_a_loaded_script_reloads_it(
    workspace: RemoteWorkspace,
    script: Path,
    mocker: MockerFixture,
) -> None:
    workspace.content = script
    workspace.set_loaded_page()
    load_content = mocker.patch.object(workspace, "load_content")
    reload_content = mocker.patch.object(workspace, "reload_content")

    QTimer.singleShot(0, workspace.dialog.accept)
    workspace._on_configure_connection()

    reload_content.assert_called_once_with()
    load_content.assert_not_called()


@pytest.fixture
def no_keyring_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Keyring()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(SecretsManager, "_keyring", property(lambda self: backend))
    monkeypatch.setattr(
        SecretsManager, "_PasswordDeleteError", property(lambda self: keyring.errors.PasswordDeleteError)
    )


def test_workspace_is_created_without_a_keyring_backend(qtbot: QtBot, no_keyring_backend: None) -> None:
    ws = RemoteWorkspace()
    qtbot.addWidget(ws)

    assert ws.stack.currentWidget() is ws.empty_page


def test_connect_without_a_keyring_backend_keeps_the_secrets_for_the_session(
    workspace: RemoteWorkspace,
    script: Path,
    mocker: MockerFixture,
    no_keyring_backend: None,
) -> None:
    remote_client = mocker.patch("vsview_remote.workspace.RemoteClient")
    messages = list[str]()
    workspace.api.statusMessage.connect(messages.append)
    shown = list[tuple[str, str]]()

    def record_and_edit() -> None:
        shown.append((workspace.dialog.token_edit.text(), workspace.dialog.status_label.text()))
        workspace.dialog.token_edit.setText("session-token")

    def record() -> None:
        shown.append((workspace.dialog.token_edit.text(), workspace.dialog.status_label.text()))

    load_content = connect_through_load_button(workspace, script, mocker, record_and_edit)
    client = workspace.client
    res = accept_dialog(workspace, record)

    load_content.assert_called_once_with(script)
    assert client is remote_client.return_value.start.return_value
    assert remote_client.call_args.kwargs["auth_token"] == "session-token"
    assert shown == [("", READ_ERROR_MESSAGE), ("session-token", "")]
    assert res is not None
    assert res.auth_token == "session-token"
    assert workspace.auth_token == "session-token"
    assert messages == [READ_ERROR_MESSAGE, READ_ERROR_MESSAGE]


def test_a_failed_keyring_read_never_deletes_the_stored_secret(
    workspace: RemoteWorkspace,
    isolate_secrets: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace.auth_token = "stored-token"
    stored = dict(isolate_secrets._storage)

    def fail_read(service: str, username: str) -> str | None:
        raise RuntimeError("keyring is locked")

    monkeypatch.setattr(isolate_secrets, "get_password", fail_read)

    res = accept_dialog(workspace)

    assert res is not None
    assert res.auth_token is None
    assert isolate_secrets._storage == stored


def test_connect_with_a_failing_keyring_write_keeps_the_secret_for_the_session(
    workspace: RemoteWorkspace,
    script: Path,
    isolate_secrets: Any,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_client = mocker.patch("vsview_remote.workspace.RemoteClient")
    messages = list[str]()
    workspace.api.statusMessage.connect(messages.append)

    def fail_write(service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring is read-only")

    monkeypatch.setattr(isolate_secrets, "set_password", fail_write)

    def edit() -> None:
        workspace.dialog.token_edit.setText("new-token")

    load_content = connect_through_load_button(workspace, script, mocker, edit)
    client = workspace.client

    load_content.assert_called_once_with(script)
    assert client is remote_client.return_value.start.return_value
    assert remote_client.call_args.kwargs["auth_token"] == "new-token"
    assert messages == [WRITE_ERROR_MESSAGE]
    assert workspace.auth_token == "new-token"
    assert isolate_secrets._storage == {}


def load_with_error(
    ws: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    mocker: MockerFixture,
    payload: RemoteErrorPayload,
) -> None:
    server = servers(error_reply(payload))
    connect_through_load_button(ws, script, mocker, enter_server(ws, server))
    ws.content = script
    with pytest.raises(RuntimeError, match="boom"):
        ws.loader()


def test_a_server_chosen_filename_is_not_read_from_the_local_disk(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    private = tmp_path / "id_ed25519"
    private.write_text("-----BEGIN KEY-----\nTOP-SECRET-KEY-MATERIAL\n-----END KEY-----\n", encoding="utf-8")
    payload = RemoteErrorPayload("ValueError: boom", "ValueError", "boom", str(private), 2, "remote line")

    load_with_error(workspace, servers, script, mocker, payload)

    assert len(dialogs) == 1
    assert f"File: <remote:{private}>:2\n>   2 | remote line" in dialogs[0]
    assert "TOP-SECRET-KEY-MATERIAL" not in dialogs[0]


def test_a_remote_frame_filename_does_not_replace_the_script(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    private = tmp_path / "notes.txt"
    private.write_text("first\nPRIVATE-NOTE\nthird\n", encoding="utf-8")
    payload = RemoteErrorPayload(
        "ValueError: boom", "ValueError", "boom", str(script), 2, frames=[StackFrame(str(private), 2)]
    )

    load_with_error(workspace, servers, script, mocker, payload)

    assert len(dialogs) == 1
    assert f"File: {script}:2" in dialogs[0]
    assert ">  2 | raise ValueError('boom')" in dialogs[0]
    assert "PRIVATE-NOTE" not in dialogs[0]


def test_a_unc_filename_never_reaches_the_filesystem(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
) -> None:
    unc = r"\\remote-test.invalid\share\x.py"
    real_exists = Path.exists

    def exists(self: Path, *args: Any, **kwargs: Any) -> bool:
        return False if str(self).startswith("\\\\") else real_exists(self, *args, **kwargs)

    spy = mocker.patch.object(Path, "exists", autospec=True, side_effect=exists)
    payload = RemoteErrorPayload("ValueError: boom", "ValueError", "boom", unc, 1)

    load_with_error(workspace, servers, script, mocker, payload)

    assert len(dialogs) == 1
    assert not any(str(call.args[0]).startswith("\\\\") for call in spy.call_args_list)


# W4-08
def test_drop_without_a_connection_shows_the_reason(
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    script: Path,
    dialogs: list[str],
    caplog: pytest.LogCaptureFixture,
    mocker: MockerFixture,
) -> None:
    def env(self: RemoteWorkspace) -> ManagedEnvironment:
        if not self._env or self._env.disposed:
            self._env = vpy_env_factory()
        return self._env

    # A subclass would lose the plugin settings model, so the property is replaced on the class itself.
    mocker.patch.object(RemoteWorkspace, "env", property(env))
    ws = RemoteWorkspace()
    qtbot.addWidget(ws)
    load_spy = mocker.spy(ws, "load_content")
    statuses = list[str]()
    ws.statusLoadingErrored.connect(statuses.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(script))])
    event = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )

    with caplog.at_level(logging.ERROR):
        ws.dropEvent(event)
        qtbot.waitUntil(lambda: load_spy.spy_return is not None and load_spy.spy_return.done(), timeout=10000)
        qtbot.waitUntil(lambda: ws.stack.currentWidget() is ws.error_page, timeout=5000)

    assert load_spy.spy_return.result() == 1
    assert statuses == ["Remote connection not configured", "Error while loading content"]
    assert len(dialogs) == 1
    assert dialogs[0].endswith("ConnectionNotConfiguredError: Remote connection not configured\n")
    assert any("Remote connection not configured" in r.getMessage() for r in caplog.records)


def test_a_script_that_is_not_utf8_shows_the_reason(
    workspace: RemoteWorkspace,
    tmp_path: Path,
    dialogs: list[str],
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "cp1252.vpy"
    path.write_bytes(b"# -*- coding: cp1252 -*-\n# caf\xe9\n")
    remote_client = mocker.patch("vsview_remote.workspace.RemoteClient")
    connect_through_load_button(workspace, path, mocker)
    workspace.content = path
    statuses = list[str]()
    workspace.statusLoadingErrored.connect(statuses.append)

    with caplog.at_level(logging.ERROR), pytest.raises(UnicodeDecodeError):
        workspace.loader()

    remote_client.assert_not_called()
    assert statuses == ["Script is not valid UTF-8"]
    assert len(dialogs) == 1
    assert "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9" in dialogs[0]
    assert any("UnicodeDecodeError" in r.getMessage() for r in caplog.records)


# W4-V02
@pytest.mark.parametrize(
    ("status", "text", "status_text"),
    [
        (StatusCode.UNAUTHORIZED, "Unauthorized: invalid or missing auth token", "Authentication failed"),
        (
            StatusCode.PERMISSION_DENIED,
            "Dynamic code evaluation is disabled on this server (allow_eval=False)",
            "Permission denied by the server",
        ),
    ],
)
def test_auth_and_permission_errors_show_the_server_message(
    workspace: RemoteWorkspace,
    servers: Callable[[Handler | None], FakeServer],
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
    status: StatusCode,
    text: str,
    status_text: str,
) -> None:
    server = servers(error_reply(RemoteErrorPayload(text, status.name, text), status))
    connect_through_load_button(workspace, script, mocker, enter_server(workspace, server))
    workspace.content = script
    statuses = list[str]()
    workspace.statusLoadingErrored.connect(statuses.append)

    with pytest.raises(PermissionError, match=re.escape(text)):
        workspace.loader()

    assert statuses == [status_text]
    assert len(dialogs) == 1
    assert dialogs[0].endswith(f"{status.name}: {text}\n")


@pytest.mark.parametrize(
    ("error", "status_text"),
    [
        (TimeoutError(), "Remote server timed out"),
        (RemoteTimeoutError("handshake timed out"), "Remote server timed out"),
        (TransportNotConnectedError("not connected"), "Connection error"),
    ],
)
def test_timeout_and_transport_errors_show_a_dialog(
    workspace: RemoteWorkspace,
    script: Path,
    dialogs: list[str],
    mocker: MockerFixture,
    error: Exception,
    status_text: str,
) -> None:
    remote_client = mocker.patch("vsview_remote.workspace.RemoteClient")
    remote_client.return_value.start.return_value.load_code.return_value.result.side_effect = error
    connect_through_load_button(workspace, script, mocker)
    workspace.content = script
    statuses = list[str]()
    workspace.statusLoadingErrored.connect(statuses.append)

    with pytest.raises(type(error)):
        workspace.loader()

    assert statuses == [status_text]
    assert len(dialogs) == 1
    assert dialogs[0].endswith(f"{type(error).__name__}: {error}\n".replace(": \n", "\n"))
