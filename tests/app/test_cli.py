from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from collections.abc import Generator
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from cyclopts import CoercionError, CycloptsError, Token, ValidationError
from pytest_mock import MockerFixture

from vsview.app.plugins.manager import PluginManager
from vsview.app.settings import SettingsManager
from vsview.app.settings.models import GlobalSettings
from vsview.app.workspace import PythonScriptWorkspace, QuickScriptWorkspace, VideoFileWorkspace
from vsview.cli import (
    CycloptsGroupError,
    _populate_path,
    app,
    convert_arg,
    convert_log_level,
    convert_qt_args,
    main_meta,
)

pytestmark = [pytest.mark.unit]


def run_cli(argv: list[str], capfd: pytest.CaptureFixture[str]) -> str:
    app(argv, exit_on_error=False, result_action="return_value")
    out, err = capfd.readouterr()
    return out + err


# Token Converters
class TestTokenConverters:
    def test_convert_arg_single_key_value(self) -> None:
        tokens = [Token(value="foo=bar")]
        assert convert_arg(dict, tokens) == {"foo": "bar"}

    def test_convert_arg_multiple_tokens_and_nested_equals(self) -> None:
        tokens = [Token(value="a=1"), Token(value="filter=b=2"), Token(value="url=https://example.com?query=true")]
        expected = {"a": "1", "filter": "b=2", "url": "https://example.com?query=true"}
        assert convert_arg(dict, tokens) == expected

    @pytest.mark.parametrize(
        ("values", "bad_values"),
        [(["no_equal_sign"], ["no_equal_sign"]), (["bad1", "k=v", "bad2"], ["bad1", "bad2"])],
        ids=["one_bad", "two_bad_one_good"],
    )
    def test_convert_arg_reports_every_malformed_token(self, values: list[str], bad_values: list[str]) -> None:
        tokens = [Token(value=v) for v in values]

        with pytest.raises(CycloptsGroupError) as exc_info:
            convert_arg(dict, tokens)

        errors = exc_info.value.exceptions
        assert all(isinstance(e, CycloptsError) for e in errors)
        assert [bad in str(e) for bad, e in zip(bad_values, errors, strict=True)] == [True] * len(bad_values)

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
        with pytest.raises(KeyError, match="NON_EXISTENT_LEVEL"):
            convert_log_level("ignored", tokens)

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            (["-platform offscreen"], ["-platform", "offscreen"]),
            (
                ["-platform offscreen", '-style "Fusion" -stylesheet "custom.qss"'],
                ["-platform", "offscreen", "-style", "Fusion", "-stylesheet", "custom.qss"],
            ),
            (["   -platform    offscreen   "], ["-platform", "offscreen"]),
        ],
        ids=["simple", "several_and_quoted", "extra_whitespace"],
    )
    def test_convert_qt_args_splits_like_a_shell(self, values: list[str], expected: list[str]) -> None:
        assert convert_qt_args(list, [Token(value=v) for v in values]) == expected


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


# Config, Settings, Version & Help Commands
CONFIG_TARGETS = {
    "config": lambda: GlobalSettings.config_path,
    "settings": lambda: GlobalSettings.path_env,
}


class TestSubcommands:
    @pytest.mark.parametrize("group", ["config", "settings"])
    def test_path_command_prints_the_path(self, capfd: pytest.CaptureFixture[str], group: str) -> None:
        output = run_cli([group, "path"], capfd)

        assert str(CONFIG_TARGETS[group]()) in output.replace("\n", "")

    @pytest.mark.parametrize("group", ["config", "settings"])
    @pytest.mark.parametrize("exists", [True, False], ids=["existing", "missing"])
    def test_wipe_command_leaves_no_target_behind(
        self, capfd: pytest.CaptureFixture[str], group: str, exists: bool
    ) -> None:
        target = CONFIG_TARGETS[group]()
        if exists:
            if group == "config":
                target.mkdir(parents=True, exist_ok=True)
                (target / "extra.json").write_text("{}", encoding="utf-8")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")

        run_cli([group, "wipe"], capfd)

        assert target.exists() is False

    @pytest.mark.parametrize(
        "argv", [["config", "help"], ["help", "config"], ["settings", "help"], ["help", "settings"]]
    )
    def test_help_lists_the_subcommands(self, capfd: pytest.CaptureFixture[str], argv: list[str]) -> None:
        output = run_cli(argv, capfd)

        assert "path" in output
        assert "wipe" in output

    def test_version_command_prints_version(self, capfd: pytest.CaptureFixture[str]) -> None:
        assert importlib.metadata.version("vsview") in run_cli(["version"], capfd)

    @pytest.mark.parametrize(("flag", "expected"), [("--version", None), ("--help", "--no-default-workspace")])
    def test_meta_flags_print_version_and_help(
        self, capfd: pytest.CaptureFixture[str], flag: str, expected: str | None
    ) -> None:
        app.meta([flag], exit_on_error=False, result_action="return_value")
        out, err = capfd.readouterr()

        assert (expected or importlib.metadata.version("vsview")) in out + err


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
        """Replace the Qt Application and MainWindow, the GUI boundary of the CLI."""
        mock_app = MagicMock()
        mock_app.exit.return_value = 0
        mock_app.exec.return_value = 0
        mocker.patch("vsview.app.main.Application", return_value=mock_app)

        mock_window = MagicMock()
        mocker.patch("vsview.app.main.MainWindow", return_value=mock_window)

        mocker.patch("vsview.assets.load_fonts")

        return mock_app, mock_window

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

    @pytest.mark.parametrize(
        ("name", "extra_args", "expected_kwargs"),
        [("video.vpy", ["-a", "bitdepth=10"], {"bitdepth": "10"}), ("video.VPY", [], {})],
        ids=["with_args", "uppercase_suffix"],
    )
    def test_cli_dispatch_loads_a_script_file(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        tmp_path: Path,
        name: str,
        extra_args: list[str],
        expected_kwargs: dict[str, str],
    ) -> None:
        _, mock_window = mock_gui_components
        script = tmp_path / name
        script.write_text("import vapoursynth as vs", encoding="utf-8")

        with pytest.raises(SystemExit, match=r"^0$"):
            app([str(script), *extra_args, "--no-default-workspace"], exit_on_error=False)

        mock_window.load_new_script.assert_called_once_with(script, **expected_kwargs)
        mock_window.load_new_file.assert_not_called()

    def test_cli_dispatch_qt_arg_passes_to_application(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        mocker: MockerFixture,
    ) -> None:
        mock_app_cls = mocker.patch("vsview.app.main.Application")
        mock_app = MagicMock()
        mock_app.exit.return_value = 0
        mock_app.exec.return_value = 0
        mock_app_cls.return_value = mock_app

        with pytest.raises(SystemExit, match=r"^0$"):
            app(["-q", '--style "Fusion"', "--no-default-workspace"], exit_on_error=False)

        mock_app_cls.assert_called_once()
        call_args = mock_app_cls.call_args[0][0]
        assert "--style" in call_args
        assert "Fusion" in call_args

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
        with pytest.raises(CoercionError, match="invalid_level_xyz"):
            app(["-vs-ll", "invalid_level_xyz"], exit_on_error=False)

    @pytest.mark.parametrize(
        "args",
        [["-w", "non_existent_workspace_xyz"], ["-w", "script", "-w", "non_existent_workspace_xyz"]],
        ids=["unknown_only", "valid_and_unknown"],
    )
    def test_main_unknown_workspace_names_it_and_exits(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        capfd: pytest.CaptureFixture[str],
        args: list[str],
    ) -> None:
        mock_app, mock_window = mock_gui_components

        with pytest.raises(SystemExit, match=r"^1$"):
            app(args, exit_on_error=False)

        out, err = capfd.readouterr()
        assert "non_existent_workspace_xyz" in out + err
        mock_window.show.assert_not_called()
        mock_window.add_workspace.assert_not_called()
        mock_app.exec.assert_not_called()

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

        mock_window.add_workspace.assert_not_called()

    def test_main_default_workspaces_created_when_no_options(
        self, mock_gui_components: tuple[MagicMock, MagicMock]
    ) -> None:
        _, mock_window = mock_gui_components

        with pytest.raises(SystemExit, match=r"^0$"):
            app([], exit_on_error=False)

        assert mock_window.add_workspace.call_args_list == [
            call(PythonScriptWorkspace),
            call(VideoFileWorkspace),
            call(QuickScriptWorkspace),
        ]

    @pytest.mark.parametrize(
        ("configured_workspaces", "expected_calls"),
        [
            (["quick-script", "script"], [call(QuickScriptWorkspace), call(PythonScriptWorkspace)]),
            (["script", "script"], [call(PythonScriptWorkspace), call(PythonScriptWorkspace)]),
            (["non_existent_wk", "script"], [call(PythonScriptWorkspace)]),
            ([], []),
        ],
        ids=["custom_order", "duplicates", "unknown_is_skipped", "none"],
    )
    def test_main_custom_default_workspaces_from_settings(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        configured_workspaces: list[str],
        expected_calls: list[Any],
    ) -> None:
        _, mock_window = mock_gui_components
        SettingsManager.global_settings.default_workspaces = configured_workspaces

        with pytest.raises(SystemExit, match=r"^0$"):
            app([], exit_on_error=False)

        assert mock_window.add_workspace.call_args_list == expected_calls

    def test_main_starts_with_builtin_workspaces_after_a_failed_plugin_load(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        init_plugin_manager: PluginManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mock_app, mock_window = mock_gui_components
        failed: Future[None] = Future()
        failed.set_exception(RuntimeError("simulated plugin load failure"))
        monkeypatch.setattr(init_plugin_manager, "_load_future", failed)
        SettingsManager.global_settings.default_workspaces = ["editor", "script"]

        with pytest.raises(SystemExit, match=r"^0$"):
            app([], exit_on_error=False)

        mock_window.add_workspace.assert_called_once_with(PythonScriptWorkspace)
        mock_app.exec.assert_called_once_with()

    def test_main_keeps_starting_when_a_default_workspace_constructor_raises(
        self, mock_gui_components: tuple[MagicMock, MagicMock]
    ) -> None:
        mock_app, mock_window = mock_gui_components
        SettingsManager.global_settings.default_workspaces = ["quick-script", "script"]
        mock_window.add_workspace.side_effect = [RuntimeError("broken workspace"), MagicMock()]

        with pytest.raises(SystemExit, match=r"^0$"):
            app([], exit_on_error=False)

        assert mock_window.add_workspace.call_args_list == [call(QuickScriptWorkspace), call(PythonScriptWorkspace)]
        mock_window.button_group.buttons.return_value[0].click.assert_called_once_with()
        mock_app.exec.assert_called_once_with()

    def test_main_meta_adds_the_python_folder_to_path_when_pyapp_is_active(
        self,
        mock_gui_components: tuple[MagicMock, MagicMock],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("PYAPP", "1")
        monkeypatch.setenv("PATH", str(tmp_path))

        with pytest.raises(SystemExit, match=r"^0$"):
            main_meta("--no-default-workspace")

        assert str(Path(sys.executable).parent) in os.environ["PATH"].split(os.pathsep)
