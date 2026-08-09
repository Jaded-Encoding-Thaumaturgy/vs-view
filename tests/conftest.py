from collections.abc import Generator
from pathlib import Path

import pytest

from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.models import GlobalSettings


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    # Redirect global settings paths to isolated pytest tmp_path
    fake_config = tmp_path / "global_settings.json"
    monkeypatch.setattr(GlobalSettings, "path", fake_config)
    monkeypatch.setattr(GlobalSettings, "path_env", fake_config)

    # Reset settings on existing instance to preserve signal connections
    _reset_settings_manager()

    try:
        yield
    finally:
        _reset_settings_manager()


def _reset_settings_manager() -> None:
    if SettingsManager in type(SettingsManager)._instances:
        sm = SettingsManager()
        sm._global_settings = sm.default_global_settings
        sm._local_settings.clear()
