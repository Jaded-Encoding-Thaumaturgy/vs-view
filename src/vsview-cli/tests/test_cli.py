from __future__ import annotations

from pathlib import Path

import pytest
from vsview_cli import parse_args


def test_basic_parsing() -> None:
    args = ["vsview", "script.py", "video.mkv", "-v"]
    res = parse_args(args)

    # PathBuf in Rust is converted to pathlib.Path in Python by PyO3
    assert res["files"] == [Path("script.py"), Path("video.mkv")]
    assert res["verbose"] == 1
    assert res["no_settings"] is False
    assert res["arg"] == {}


def test_verbosity_levels() -> None:
    assert parse_args(["vsview", "-v"])["verbose"] == 1
    assert parse_args(["vsview", "-vv"])["verbose"] == 2
    assert parse_args(["vsview", "-vvv"])["verbose"] == 3


def test_script_args() -> None:
    args = ["vsview", "--arg", "key1=value1", "-a", "key2=value2"]
    res = parse_args(args)

    assert res["arg"] == {"key1": "value1", "key2": "value2"}


def test_invalid_script_arg_key() -> None:
    # '1key' is not a valid Python identifier
    args = ["vsview", "--arg", "1key=value"]
    with pytest.raises(SystemExit) as excinfo:
        parse_args(args)
    assert excinfo.value.code != 0


def test_reserved_keyword_arg() -> None:
    # 'class' is a reserved Python keyword
    args = ["vsview", "--arg", "class=value"]
    with pytest.raises(SystemExit) as excinfo:
        parse_args(args)
    assert excinfo.value.code != 0


def test_complex_args() -> None:
    args = [
        "vsview",
        "--no-settings",
        "--settings-env",
        "--settings-env-copy",
        "-q",
        "arg1",
        "-q",
        "arg2",
    ]
    res = parse_args(args)

    assert res["no_settings"] is True
    assert res["settings_env"] is True
    assert res["settings_env_copy"] is True
    assert res["qt_arg"] == ["arg1", "arg2"]


def test_help_exit() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["vsview", "--help"])
    assert excinfo.value.code == 0


def test_version_exit() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["vsview", "version"])
    assert excinfo.value.code == 0


def test_settings_subcommands() -> None:
    res = parse_args(["vsview", "settings", "path"])
    assert res["settings"]["path"] is True

    res = parse_args(["vsview", "settings", "wipe"])
    assert res["settings"]["wipe"]["all"] is False

    res = parse_args(["vsview", "settings", "wipe", "--all"])
    assert res["settings"]["wipe"]["all"] is True
