from __future__ import annotations

from collections.abc import Generator

import keyring.errors
import pytest
from keyring.backends.fail import Keyring
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from vsview_remote.workspace import RemoteWorkspace

from vsview.app.main import MainWindow
from vsview.app.settings.secrets import SecretsManager
from vsview.app.workspace import PythonScriptWorkspace
from vsview.vsenv import QtEventLoop, unregister_policy

pytestmark = [pytest.mark.integration, pytest.mark.qt, pytest.mark.vpy("no-policy")]


@pytest.fixture(autouse=True)
def ensure_no_policy() -> Generator[None]:
    try:
        yield
    finally:
        unregister_policy()


def test_new_remote_workspace_without_a_keyring_backend(
    qapp: QApplication,
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow()
    window.stack.animations_enabled = False
    qtbot.addWidget(window)
    window.show()
    window.add_workspace(PythonScriptWorkspace)
    backend = Keyring()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(SecretsManager, "_keyring", property(lambda self: backend))
    monkeypatch.setattr(
        SecretsManager, "_PasswordDeleteError", property(lambda self: keyring.errors.PasswordDeleteError)
    )

    button = window.add_workspace(RemoteWorkspace)

    assert isinstance(button.workspace, RemoteWorkspace)
    assert window.stack.currentWidget() is button.workspace
    assert window.findChildren(RemoteWorkspace) == [button.workspace]
