from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsview_remote.dialog import ConnectionConfig
from vsview_remote.workspace import RemoteWorkspace

from vsview.app.plugins.manager import PluginManager
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.qt, pytest.mark.integration, pytest.mark.vpy("no-core")]

SERVER_KEY = "S" * 40
PUBLIC_KEY = "P" * 40
SECRET_KEY = "c" * 40


@pytest.fixture(autouse=True)
def _setup(qt_event_loop: QtEventLoop, init_plugin_manager: PluginManager) -> None:
    return None


@pytest.fixture
def workspace(qtbot: QtBot) -> RemoteWorkspace:
    # qtbot calls deleteLater at teardown, which closes any live client.
    ws = RemoteWorkspace()
    qtbot.addWidget(ws)
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
) -> MagicMock:
    mocker.patch.object(QFileDialog, "getOpenFileName", return_value=(str(script), ""))
    load_content = mocker.patch.object(ws, "load_content")

    def press_connect() -> None:
        if before_accept is not None:
            before_accept()
        ws.dialog.accept()

    QTimer.singleShot(0, press_connect)
    ws.load_btn.click()
    return load_content


def store_curve_keys(ws: RemoteWorkspace, *, use_curve: bool) -> None:
    ws.settings.global_.use_curve = use_curve
    ws.settings.global_.curve_server_key = SERVER_KEY
    ws.settings.global_.curve_public_key = PUBLIC_KEY
    ws.curve_secret_key = SECRET_KEY


# W4-03
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
