from __future__ import annotations

import faulthandler
import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from itertools import chain
from logging import DEBUG, getLogger
from pathlib import Path
from signal import SIG_DFL, SIGINT, signal

from pydantic import BaseModel
from vsview_cli import parse_args

from .app.main import Application, MainWindow
from .app.plugins.manager import PluginManager
from .app.settings.models import GlobalSettings
from .assets import load_fonts
from .logging import console, setup_logging

logger = getLogger(__name__)

# Enable faulthandler to get stack traces on segfaults
faulthandler.enable(file=console.file)


class CLIConfig(BaseModel):
    settings: SettingsCommand | None
    files: list[Path]
    no_settings: bool
    settings_roaming: bool
    settings_env: bool
    settings_env_copy: bool
    verbose: int
    arg: dict[str, str]
    qt_arg: list[str]


class SettingsCommand(BaseModel):
    path: bool = False
    wipe: SettingsWipeCommand | None = None


class SettingsWipeCommand(BaseModel):
    all: bool = False


def main(argv: Sequence[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    raw = parse_args(["vsview", *argv], shutil.get_terminal_size().columns)
    cfg = CLIConfig.model_validate(raw)

    if cfg.settings:
        if cfg.settings.path:
            console.print(GlobalSettings.path_env)
        if cfg.settings.wipe:
            GlobalSettings.path_env.unlink(missing_ok=True)
            console.print("Global config file sucessfully deleted.")

            if cfg.settings.wipe.all:
                GlobalSettings.config_path.rmdirs(missing_ok=True, ignore_errors=True)
                console.print("Global config path sucessfully deleted.")
        raise SystemExit(0)

    # Setup env vars
    os.environ["JETPYTOOLS_NO_COLOR"] = "1"
    os.environ["PYDANTIC_ERRORS_INCLUDE_URL"] = "false"

    if cfg.settings_roaming:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ROAMING"] = "1"
    if cfg.settings_env:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT"] = "1"
    if cfg.settings_env_copy:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"] = "1"

    # -v -> DEBUG, -vv -> DEBUG - 1, -vvv -> DEBUG - 2, etc.
    setup_logging(level=DEBUG - max(0, cfg.verbose - 1) if cfg.verbose else None)

    # Set signal handler to default to allow Ctrl+C to work
    signal(SIGINT, SIG_DFL)

    app = Application(
        # TODO: This parsing could  probably be moved to the rust parser
        [sys.argv[0], *chain.from_iterable(shlex.split(q) for q in cfg.qt_arg)],
        no_settings=cfg.no_settings,
    )

    PluginManager.load()
    load_fonts()

    main_window = MainWindow()
    main_window.ensurePolished()

    if cfg.files:
        main_window.show()
        for file in cfg.files:
            if file.suffix in [".py", ".vpy"]:
                main_window.load_new_script(file, **cfg.arg)
            else:
                main_window.load_new_file(file)
    else:
        # Show window first for faster perceived startup
        main_window.show()
        main_window.repaint()
        app.processEvents()

        # Now create default workspaces
        main_window.script_subaction.trigger()
        main_window.file_subaction.trigger()
        main_window.stack.animations_enabled = False
        main_window.quick_script_subaction.trigger()
        main_window.button_group.buttons()[0].click()
        main_window.stack.animations_enabled = True

    raise SystemExit(app.exec())
