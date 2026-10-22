from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytestqt.qtbot import QtBot
from vsview_remote._metadata import WORKSPACE_ID
from vsview_remote.dialog import ConnectionConfig
from vsview_remote.settings import GlobalSettings as RemoteGlobalSettings
from vsview_remote.workspace import RemoteWorkspace

from vsview.api import SecretLineEdit
from vsview.app.outputs import VideoMetadata
from vsview.app.settings.models import extract_settings
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit]


def test_remote_global_settings_secret_fields() -> None:
    settings = RemoteGlobalSettings()
    assert settings.auth_token is None
    assert settings.curve_secret_key is None

    entries = extract_settings(RemoteGlobalSettings, section="Remote")
    entry_dict = {e.key: e.metadata for e in entries}

    assert "auth_token" in entry_dict
    assert isinstance(entry_dict["auth_token"], SecretLineEdit)
    assert entry_dict["auth_token"].namespace == WORKSPACE_ID
    assert entry_dict["auth_token"].context == "auth"
    assert entry_dict["auth_token"].key == "token"

    assert "curve_secret_key" in entry_dict
    assert isinstance(entry_dict["curve_secret_key"], SecretLineEdit)
    assert entry_dict["curve_secret_key"].namespace == WORKSPACE_ID
    assert entry_dict["curve_secret_key"].context == "curve"
    assert entry_dict["curve_secret_key"].key == "secret_key"


def test_remote_workspace_lifecycle_and_reconfiguration(qtbot: QtBot, qt_event_loop: QtEventLoop) -> None:
    workspace = RemoteWorkspace()
    qtbot.addWidget(workspace)

    workspace._output_metadata[0] = VideoMetadata("Test")
    assert len(workspace.get_output_metadata()) == 1
    workspace.clear_environment()
    assert len(workspace.get_output_metadata()) == 0

    dummy_client = MagicMock()
    workspace._client = dummy_client

    new_cfg = ConnectionConfig(
        address="tcp://10.0.0.1:5555",
        auth_token="test_token",
        compression="zstd",
        prefetch=8,
        backlog=24,
        forward_logs=False,
        subscribe_streams=False,
        use_curve=False,
        curve_server_key=None,
        curve_public_key=None,
        curve_secret_key=None,
    )

    workspace.prompt_connection_dialog = MagicMock(return_value=new_cfg)
    workspace.reload_content = MagicMock()

    workspace._on_configure_connection()

    assert workspace._config == new_cfg
    dummy_client.close.assert_called_once()
    assert workspace._client is None
