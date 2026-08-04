from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class CLIConfig(BaseModel):
    settings: SettingsCommand | None
    files: list[Path]
    workspace: list[str]
    no_default_workspace: bool
    no_settings: bool
    settings_roaming: bool
    settings_env: bool
    settings_env_copy: bool
    verbose: int
    arg: dict[str, str]
    qt_arg: list[str]
    hdr: bool
    file_log: bool
    vapoursynth_log_level: int | None
    vsengine_log_level: int | None
    qt_log_level: int | None


class SettingsCommand(BaseModel):
    path: bool = False
    wipe: SettingsWipeCommand | None = None


class SettingsWipeCommand(BaseModel):
    all: bool = False
