from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from collections.abc import Generator
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cyclopts import CoercionError, Token, ValidationError
from pytest_mock import MockerFixture

from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.models import GlobalSettings
from vsview.app.workspace import PythonScriptWorkspace
from vsview.cli import (
    _populate_path,
    app,
    convert_arg,
    convert_log_level,
    convert_qt_args,
    main,
    main_meta,
    settings_app,
    settings_path,
    settings_wipe,
)
from vsview.cli import help as cli_help
from vsview.cli import version as cli_version

pytestmark = [pytest.mark.unit]


# Token Converters
class TestTokenConverters:
    def test_convert_arg_single_key_value(self) -> None:
        tokens = [Token(value="foo=bar")]
        assert convert_arg(dict, tokens) == {"foo": "bar"}

    def test_convert_arg_multiple_tokens_and_nested_equals(self) -> None:
        tokens = [Token(value="a=1"), Token(value="filter=b=2"), Token(value="url=https://example.com?query=true")]
        expected = {"a": "1", "filter": "b=2", "url": "https://example.com?query=true"}
        assert convert_arg(dict, tokens) == expected

    def test_convert_arg_malformed_token_raises_value_error(self) -> None:
        tokens = [Token(value="malformed_token_without_equal")]
        with pytest.raises(ValueError, match=r"not enough values to unpack"):
            convert_arg(dict, tokens)

    @pytest.mark.parametrize(
        ("level_str", "expected_int"),
        [
            ("debug", logging.DEBUG),
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
            ("critical", logging.CRITICAL),
        ],
    )
    def test_convert_log_level_valid_levels(self, level_str: str, expected_int: int) -> None:
        tokens = [Token(value=level_str)]
        assert convert_log_level("ignored", tokens) == expected_int

    def test_convert_log_level_invalid_raises_key_error(self) -> None:
        tokens = [Token(value="non_existent_level")]
        with pytest.raises(KeyError, match=r"^'NON_EXISTENT_LEVEL'$"):
            convert_log_level("ignored", tokens)

    def test_convert_qt_args_simple(self) -> None:
        tokens = [Token(value="-platform offscreen")]
        assert convert_qt_args(list, tokens) == ["-platform", "offscreen"]

    def test_convert_qt_args_multiple_and_quoted(self) -> None:
        tokens = [
            Token(value="-platform offscreen"),
            Token(value='-style "Fusion" -stylesheet "custom.qss"'),
        ]
        expected = ["-platform", "offscreen", "-style", "Fusion", "-stylesheet", "custom.qss"]
        assert convert_qt_args(list, tokens) == expected

    def test_convert_qt_args_extra_whitespace(self) -> None:
        tokens = [Token(value="   -platform    offscreen   ")]
        assert convert_qt_args(list, tokens) == ["-platform", "offscreen"]


# Path Population Helper
class TestPopulatePath:
    def test_populate_path_prepends_bin_directory_without_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "python_dir"
        fake_bin.mkdir()
        fake_exe = fake_bin / "python.exe"
        existing_dir = tmp_path / "existing_bin"
        existing_dir.mkdir()

        monkeypatch.setattr(sys, "executable", str(fake_exe))
        monkeypatch.setenv("PATH", str(existing_dir))

        _populate_path()

        new_path = os.environ["PATH"].split(os.pathsep)
        assert new_path[0] == str(fake_bin.resolve())
        assert str(existing_dir) in new_path

    def test_populate_path_prepends_scripts_directory_on_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_bin = tmp_path / "python_dir"
        fake_bin.mkdir()
        scripts_dir = fake_bin / "Scripts"
        scripts_dir.mkdir()
        fake_exe = fake_bin / "python.exe"
        existing_dir = tmp_path / "existing_bin"
        existing_dir.mkdir()

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(fake_exe))
        monkeypatch.setenv("PATH", str(existing_dir))

        _populate_path()

        new_path = os.environ["PATH"].split(os.pathsep)
        assert new_path[0] == str(scripts_dir.resolve())
        assert new_path[1] == str(fake_bin.resolve())
        assert str(existing_dir) in new_path

    def test_populate_path_does_not_duplicate_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_bin = tmp_path / "python_dir"
        fake_bin.mkdir()
        fake_exe = fake_bin / "python.exe"

        resolved_bin = str(fake_bin.resolve())
        monkeypatch.setattr(sys, "executable", str(fake_exe))
        monkeypatch.setenv("PATH", resolved_bin)

        _populate_path()

        path_parts = os.environ["PATH"].split(os.pathsep)
        assert path_parts.count(resolved_bin) == 1


# Settings Subcommands
class TestSettingsCommands:
    def test_settings_path_command_prints_path(self, capfd: pytest.CaptureFixture[str]) -> None:
        settings_path()
        out, err = capfd.readouterr()
        combined = (out + err).replace("\n", "")
        assert str(GlobalSettings.path_env).replace("\n", "") in combined

    def test_settings_path_cli_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        app(["settings", "path"], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()
        combined = (out + err).replace("\n", "")
        assert str(GlobalSettings.path_env).replace("\n", "") in combined

    def test_settings_wipe_unlinks_existing_file(self, capfd: pytest.CaptureFixture[str]) -> None:
        GlobalSettings.path_env.parent.mkdir(parents=True, exist_ok=True)
        GlobalSettings.path_env.write_text("{}", encoding="utf-8")
        assert GlobalSettings.path_env.exists() is True

        settings_wipe()

        assert GlobalSettings.path_env.exists() is False
        out, err = capfd.readouterr()
        assert "Global config file successfully deleted." in (out + err)

    def test_settings_wipe_cli_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        GlobalSettings.path_env.parent.mkdir(parents=True, exist_ok=True)
        GlobalSettings.path_env.write_text("{}", encoding="utf-8")
        assert GlobalSettings.path_env.exists() is True

        app(["settings", "wipe"], exit_on_error=False, result_action="return_value")

        assert GlobalSettings.path_env.exists() is False
        out, err = capfd.readouterr()
        assert "Global config file successfully deleted." in (out + err)

    def test_settings_wipe_handles_missing_file_gracefully(self, capfd: pytest.CaptureFixture[str]) -> None:
        if GlobalSettings.path_env.exists():
            GlobalSettings.path_env.unlink()

        settings_wipe()

        out, err = capfd.readouterr()
        assert "No global config file found." in (out + err)

    def test_settings_wipe_all_removes_directory(self, capfd: pytest.CaptureFixture[str]) -> None:
        GlobalSettings.config_path.mkdir(parents=True, exist_ok=True)
        (GlobalSettings.config_path / "extra.json").write_text("{}", encoding="utf-8")

        settings_wipe(all=True)

        assert GlobalSettings.config_path.exists() is False
        out, err = capfd.readouterr()
        assert "Global config path successfully deleted." in (out + err)

    def test_settings_wipe_all_cli_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        GlobalSettings.config_path.mkdir(parents=True, exist_ok=True)
        (GlobalSettings.config_path / "extra.json").write_text("{}", encoding="utf-8")

        app(["settings", "wipe", "--all"], exit_on_error=False, result_action="return_value")

        assert GlobalSettings.config_path.exists() is False
        out, err = capfd.readouterr()
        assert "Global config path successfully deleted." in (out + err)

    def test_settings_help_subcommand(self, capfd: pytest.CaptureFixture[str]) -> None:
        settings_app(["help"], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()
        combined = out + err
        assert "path" in combined
        assert "wipe" in combined


# Version & Help Commands
class TestVersionAndHelpCommands:
    def test_version_command_prints_version(self, capfd: pytest.CaptureFixture[str]) -> None:
        cli_version()
        out, err = capfd.readouterr()
        output = (out + err).strip()
        expected_version = importlib.metadata.version("vsview")
        assert expected_version in output

    def test_version_cli_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        app(["version"], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()
        output = (out + err).strip()
        expected_version = importlib.metadata.version("vsview")
        assert expected_version in output

    def test_version_meta_flag_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        app.meta(["--version"], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()
        output = (out + err).strip()
        expected_version = importlib.metadata.version("vsview")
        assert expected_version in output

    def test_help_command_prints_help(self, capfd: pytest.CaptureFixture[str]) -> None:
        cli_help("settings")
        out, err = capfd.readouterr()
        output = out + err
        assert "Settings management" in output or "path" in output

    def test_help_meta_flag_dispatch(self, capfd: pytest.CaptureFixture[str]) -> None:
        app.meta(["--help"], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()
        output = out + err
        assert "Usage:" in output
        assert "Options:" in output
        assert "Workspace Options:" in output


# Main Entry Point Argument Handling & Environment Setup
class TestMainCommandRouting:
    @pytest.fixture
    def isolate_cli_env(self) -> Generator[None]:
        """Guarantee automated teardown of all environment variables mutated by CLI main."""
        orig_env = os.environ.copy()
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(orig_env)

    @pytest.fixture
    def mock_gui_components(
        self,
        mocker: MockerFixture,
        init_plugin_manager: PluginManager,
    ) -> tuple[MagicMock, MagicMock]:
        """Mock Qt Application and MainWindow without monkeypatching PluginManager singletons."""
        mock_app = MagicMock()
        mock_app.exit.return_value = 0
        mock_app.exec.return_value = 0
        mocker.patch("vsview.app.main.Application", return_value=mock_app)

        mock_window = MagicMock()
        mocker.patch("vsview.app.main.MainWindow", return_value=mock_window)

        # Pre-resolve PluginManager loading future without monkeypatching workspaces property or registry
        load_future: Future[None] = Future()
        load_future.set_result(None)
        init_plugin_manager._load_future = load_future

        mocker.patch("vsview.assets.load_fonts")

        return mock_app, mock_window

    def test_main_sets_environment_flags(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        isolate_cli_env: None,
    ) -> None:
        with pytest.raises(SystemExit, match=r"^0$"):
            main(
                hdr=True,
                settings_roaming=True,
                settings_env=True,
                settings_env_copy=True,
                no_default_workspace=True,
            )

        assert os.environ.get("VSVIEW_HDR") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ROAMING") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY") == "true"

    def test_cli_dispatch_sets_environment_flags(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        isolate_cli_env: None,
    ) -> None:
        with pytest.raises(SystemExit, match=r"^0$"):
            app(
                [
                    "--hdr",
                    "--settings-roaming",
                    "--settings-env",
                    "--settings-env-copy",
                    "--no-default-workspace",
                ],
                exit_on_error=False,
            )

        assert os.environ.get("VSVIEW_HDR") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ROAMING") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT") == "true"
        assert os.environ.get("VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY") == "true"

    def test_cli_dispatch_script_file_with_args(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        tmp_path: Path,
    ) -> None:
        _, mock_window = mock_gui_components
        script = tmp_path / "video.vpy"
        script.write_text("import vapoursynth as vs", encoding="utf-8")

        with pytest.raises(SystemExit, match=r"^0$"):
            app([str(script), "-a", "bitdepth=10", "--no-default-workspace"], exit_on_error=False)

        mock_window.load_new_script.assert_called_once_with(script, bitdepth="10")
        mock_window.load_new_file.assert_not_called()

    def test_cli_dispatch_media_file(self, mock_gui_components: tuple[MagicMock, MagicMock], tmp_path: Path) -> None:
        _, mock_window = mock_gui_components
        media = tmp_path / "movie.mkv"
        media.write_bytes(b"\x1a\x45\xdf\xa3")

        with pytest.raises(SystemExit, match=r"^0$"):
            app([str(media), "--no-default-workspace"], exit_on_error=False)

        mock_window.load_new_file.assert_called_once_with(media)
        mock_window.load_new_script.assert_not_called()

    def test_cli_validation_rejects_nonexistent_file(self) -> None:
        with pytest.raises(ValidationError, match=r"does not exist"):
            app(["nonexistent_script.vpy"], exit_on_error=False)

    def test_cli_validation_rejects_directory_as_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match=r"is a directory"):
            app([str(tmp_path)], exit_on_error=False)

    def test_cli_validation_rejects_invalid_log_level(self) -> None:
        with pytest.raises(CoercionError, match=r"Invalid value"):
            app(["-vs-ll", "invalid_level_xyz"], exit_on_error=False)

    def test_main_unknown_workspace_logs_critical_and_exits(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        mock_app, _ = mock_gui_components
        mock_app.exit.return_value = 1

        with pytest.raises(SystemExit, match=r"^1$") as exc_info:
            app(["-w", "non_existent_workspace_xyz"], exit_on_error=False)

        assert exc_info.value.code == 1
        out, err = capfd.readouterr()
        combined = out + err
        assert "non_existent_workspace_xyz" in combined
        assert "doesn't exist" in combined
        mock_app.exit.assert_called_once_with(1)

    def test_main_valid_workspace_adds_workspace(self, mock_gui_components: tuple[MagicMock, MagicMock]) -> None:
        _, mock_window = mock_gui_components

        with pytest.raises(SystemExit, match=r"^0$"):
            app(["-w", "script"], exit_on_error=False)

        mock_window.add_workspace.assert_called_once_with(PythonScriptWorkspace)

    def test_main_no_default_workspace_flag_prevents_default_actions(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
    ) -> None:
        _, mock_window = mock_gui_components

        with pytest.raises(SystemExit, match=r"^0$"):
            app(["--no-default-workspace"], exit_on_error=False)

        mock_window.script_subaction.trigger.assert_not_called()
        mock_window.file_subaction.trigger.assert_not_called()
        mock_window.quick_script_subaction.trigger.assert_not_called()

    def test_main_default_workspaces_created_when_no_options(
        self, mock_gui_components: tuple[MagicMock, MagicMock]
    ) -> None:
        _, mock_window = mock_gui_components

        with pytest.raises(SystemExit, match=r"^0$"):
            app([], exit_on_error=False)

        mock_window.script_subaction.trigger.assert_called_once()
        mock_window.file_subaction.trigger.assert_called_once()
        mock_window.quick_script_subaction.trigger.assert_called_once()

    def test_main_meta_triggers_path_population_when_pyapp_active(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        mocker: MockerFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PYAPP", "1")
        populate_spy = mocker.patch("vsview.cli._populate_path")

        with pytest.raises(SystemExit, match=r"^0$"):
            main_meta("--no-default-workspace")

        populate_spy.assert_called_once()
