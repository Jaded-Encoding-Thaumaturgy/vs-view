from __future__ import annotations

import pytest
from vsview_remote._metadata import WORKSPACE_ID
from vsview_remote.settings import GlobalSettings as RemoteGlobalSettings

from vsview.api import SecretLineEdit
from vsview.app.settings.models import extract_settings

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
