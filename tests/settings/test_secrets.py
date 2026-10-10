from __future__ import annotations

from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel
from PySide6.QtWidgets import QLineEdit
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.api import SecretLineEdit
from vsview.app.settings.models import extract_settings
from vsview.app.settings.secrets import SecretsError, SecretsManager

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class MockSecretModel(BaseModel):
    __section__: ClassVar[str] = "Test"

    token: Annotated[
        str | None,
        SecretLineEdit(
            label="Token",
            namespace="test_ns",
            context="auth",
            key="token",
            placeholder_text="Enter token",
            tooltip="Auth token tooltip",
        ),
    ] = None


def test_secret_line_edit_create_widget(qtbot: QtBot) -> None:
    meta = SecretLineEdit(
        label="Secret",
        namespace="ns",
        context="ctx",
        key="key",
        placeholder_text="Enter secret...",
        tooltip="Secret tooltip",
    )

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    assert isinstance(widget, QLineEdit)
    assert widget.echoMode() == QLineEdit.EchoMode.PasswordEchoOnEdit
    assert widget.placeholderText() == "Enter secret..."
    assert widget.toolTip() == "Secret tooltip"


def test_secret_line_edit_load_value(mocker: MockerFixture, qtbot: QtBot) -> None:
    get_spy = mocker.patch.object(SecretsManager, "get", return_value="super_secret_123")

    meta = SecretLineEdit(
        label="Secret",
        namespace="my_namespace",
        context="my_context",
        key="my_key",
    )

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    meta.load_value(widget, None)

    get_spy.assert_called_once_with("my_namespace", "my_context", "my_key")
    assert widget.text() == "super_secret_123"


def test_secret_line_edit_load_value_none(mocker: MockerFixture, qtbot: QtBot) -> None:
    get_spy = mocker.patch.object(SecretsManager, "get", return_value=None)

    meta = SecretLineEdit(
        label="Secret",
        namespace="my_namespace",
        context="my_context",
        key="my_key",
    )

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    widget.setText("old_text")
    meta.load_value(widget, None)

    get_spy.assert_called_once_with("my_namespace", "my_context", "my_key")
    assert widget.text() == ""


def test_secret_line_edit_get_value_sets_secret(mocker: MockerFixture, qtbot: QtBot) -> None:
    set_spy = mocker.patch.object(SecretsManager, "set")
    delete_spy = mocker.patch.object(SecretsManager, "delete")

    meta = SecretLineEdit(
        label="Secret",
        namespace="my_namespace",
        context="my_context",
        key="my_key",
    )

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    widget.setText("  new_secret_val  ")

    ret = meta.get_value(widget)
    assert ret is None
    set_spy.assert_called_once_with("my_namespace", "my_context", "my_key", "new_secret_val")
    delete_spy.assert_not_called()


def test_secret_line_edit_get_value_deletes_when_empty(mocker: MockerFixture, qtbot: QtBot) -> None:
    set_spy = mocker.patch.object(SecretsManager, "set")
    delete_spy = mocker.patch.object(SecretsManager, "delete")

    meta = SecretLineEdit(
        label="Secret",
        namespace="my_namespace",
        context="my_context",
        key="my_key",
    )

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    widget.setText("   ")

    ret = meta.get_value(widget)
    assert ret is None
    delete_spy.assert_called_once_with("my_namespace", "my_context", "my_key")
    set_spy.assert_not_called()


def test_extract_settings_secret_fields() -> None:
    entries = extract_settings(MockSecretModel, section="Test")
    entry_dict = {e.key: e.metadata for e in entries}

    assert "token" in entry_dict
    assert isinstance(entry_dict["token"], SecretLineEdit)
    assert entry_dict["token"].namespace == "test_ns"
    assert entry_dict["token"].context == "auth"
    assert entry_dict["token"].key == "token"


def test_model_dump_json_does_not_contain_secrets(mocker: MockerFixture, qtbot: QtBot) -> None:
    mocker.patch.object(SecretsManager, "get", return_value="very_secret_token")
    mocker.patch.object(SecretsManager, "set")
    mocker.patch.object(SecretsManager, "delete")
    meta = SecretLineEdit(label="Token", namespace="test_ns", context="auth", key="token")

    widget = meta.create_widget()
    qtbot.addWidget(widget)
    meta.load_value(widget, None)
    # The widget has the secret in memory
    assert widget.text() == "very_secret_token"

    # When get_value is called, it returns None for the model
    val = meta.get_value(widget)
    assert val is None

    model = MockSecretModel(token=val)
    dumped = model.model_dump_json()
    assert "very_secret_token" not in dumped


def test_secrets_manager_get(mocker: MockerFixture) -> None:
    mock_keyring = mocker.MagicMock()
    mock_keyring.get_password.return_value = "my_token"
    mocker.patch.object(SecretsManager, "keyring", mock_keyring)

    res = SecretsManager.get("test_ns", "auth", "token")
    assert res == "my_token"
    mock_keyring.get_password.assert_called_once_with("jet.vsview.test_ns/auth", "token")


def test_secrets_manager_set(mocker: MockerFixture) -> None:
    mock_keyring = mocker.MagicMock()
    mocker.patch.object(SecretsManager, "keyring", mock_keyring)

    SecretsManager.set("test_ns", "auth", "token", "new_secret")
    mock_keyring.delete_password.assert_called_once_with("jet.vsview.test_ns/auth", "token")
    mock_keyring.set_password.assert_called_once_with("jet.vsview.test_ns/auth", "token", "new_secret")


def test_secrets_manager_set_empty_username_raises() -> None:
    with pytest.raises(ValueError, match="Empty usernames are prohibited"):
        SecretsManager.set("test_ns", "auth", "", "new_secret")


def test_secrets_manager_delete(mocker: MockerFixture) -> None:
    mock_keyring = mocker.MagicMock()
    mocker.patch.object(SecretsManager, "keyring", mock_keyring)

    SecretsManager.delete("test_ns", "auth", "token")
    mock_keyring.delete_password.assert_called_once_with("jet.vsview.test_ns/auth", "token")


def test_secrets_manager_json(mocker: MockerFixture) -> None:
    mock_keyring = mocker.MagicMock()
    mock_keyring.get_password.return_value = '{"foo": "bar", "num": 42}'
    mocker.patch.object(SecretsManager, "keyring", mock_keyring)

    data = SecretsManager.get_json("test_ns", "auth", "config")
    assert data == {"foo": "bar", "num": 42}

    SecretsManager.set_json("test_ns", "auth", "config", {"key": "val"})
    mock_keyring.set_password.assert_called_with("jet.vsview.test_ns/auth", "config", '{"key":"val"}')

    SecretsManager.delete_json("test_ns", "auth", "config")
    mock_keyring.delete_password.assert_called_with("jet.vsview.test_ns/auth", "config")


def test_secrets_manager_error_handling(mocker: MockerFixture) -> None:
    mock_keyring = mocker.MagicMock()
    mock_keyring.get_password.side_effect = Exception("Backend failure")
    mock_keyring.set_password.side_effect = Exception("Backend failure")
    mock_keyring.delete_password.side_effect = Exception("Backend failure")
    mocker.patch.object(SecretsManager, "keyring", mock_keyring)
    mocker.patch.object(SecretsManager, "KeyringError", Exception)
    mocker.patch.object(SecretsManager, "PasswordDeleteError", KeyError)

    with pytest.raises(SecretsError, match="Failed to read secret"):
        SecretsManager.get("test_ns", "auth", "token")

    with pytest.raises(SecretsError, match="Failed to delete secret"):
        SecretsManager.delete("test_ns", "auth", "token")

    with pytest.raises(SecretsError, match="Failed to delete secret"):
        SecretsManager.set("test_ns", "auth", "token", "secret")


def test_secrets_manager_in_memory_integration() -> None:
    SecretsManager.set("test_ns", "auth", "api_key", "secret_123")
    assert SecretsManager.get("test_ns", "auth", "api_key") == "secret_123"

    SecretsManager.delete("test_ns", "auth", "api_key")
    assert SecretsManager.get("test_ns", "auth", "api_key") is None
