from __future__ import annotations

import faulthandler
import io
import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from contextlib import suppress
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
from .env import getenv_bool, load_dotenv
from .logging import console, setup_basic_logging, setup_logging

setup_basic_logging()

logger = getLogger(__name__)

# Enable faulthandler to get stack traces on segfaults
for stream in (console.file, sys.stderr, sys.__stderr__):
    if not stream:
        continue

    with suppress(AttributeError, OSError, RuntimeError, ValueError, io.UnsupportedOperation):
        stream.fileno()
        faulthandler.enable(file=stream)
        break


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
    hdr: bool


class SettingsCommand(BaseModel):
    path: bool = False
    wipe: SettingsWipeCommand | None = None


class SettingsWipeCommand(BaseModel):
    all: bool = False


def main(argv: Sequence[str] | None = None) -> None:
    if not getenv_bool("VSVIEW_NO_DOTENV", False):
        load_dotenv()

    if argv is None:
        argv = sys.argv[1:]

    raw = parse_args(["vsview", *argv], shutil.get_terminal_size().columns)
    cfg = CLIConfig.model_validate(raw)

    if cfg.settings:
        if cfg.settings.path:
            console.print(GlobalSettings.path_env)
        if cfg.settings.wipe:
            GlobalSettings.path_env.unlink(missing_ok=True)
            console.print("Global config file successfully deleted.")

            if cfg.settings.wipe.all:
                GlobalSettings.config_path.rmdirs(missing_ok=True, ignore_errors=True)
                console.print("Global config path successfully deleted.")
        raise SystemExit(0)

    # Setup env vars
    os.environ["JETPYTOOLS_NO_COLOR"] = "true"
    os.environ["PYDANTIC_ERRORS_INCLUDE_URL"] = "false"
    if cfg.hdr:
        os.environ.setdefault("QSG_RHI_HDR", "p3" if sys.platform == "darwin" else "scrgb")
        os.environ.setdefault("QSG_INFO", "1")
        os.environ.setdefault("QSG_RHI_DEBUG_LAYER", "1")
        os.environ.setdefault("QSG_RHI_LEAK_CHECK", "1")
        os.environ.setdefault("QSG_RHI_PROFILE", "1")
        if sys.platform == "linux":
            os.environ.setdefault("QSG_RENDER_LOOP", "basic")

    if cfg.settings_roaming:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ROAMING"] = "true"
    if cfg.settings_env:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT"] = "true"
    if cfg.settings_env_copy:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"] = "true"
    if cfg.hdr:
        os.environ["VSVIEW_HDR"] = "true"

    # -v -> DEBUG, -vv -> DEBUG - 1, -vvv -> DEBUG - 2, etc.
    setup_logging(level=DEBUG - max(0, cfg.verbose - 1) if cfg.verbose else None)

    # Set signal handler to default to allow Ctrl+C to work
    signal(SIGINT, SIG_DFL)

    if cfg.hdr:
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

        match sys.platform:
            case "win32":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Direct3D12)
            case "linux":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Vulkan)
            case "darwin":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Metal)

    app = Application(
        # TODO: This parsing could  probably be moved to the rust parser
        [sys.argv[0], *chain.from_iterable(shlex.split(q) for q in cfg.qt_arg)],
        no_settings=cfg.no_settings,
    )

    PluginManager.load()
    load_fonts()

    main_window = MainWindow()
    # Show window first for faster perceived startup
    main_window.show()

    if cfg.files:
        for file in cfg.files:
            if file.suffix in [".py", ".vpy"]:
                main_window.load_new_script(file, **cfg.arg)
            else:
                main_window.load_new_file(file)
    else:
        app.processEvents()
        # Now create default workspaces
        main_window.script_subaction.trigger()
        main_window.file_subaction.trigger()
        main_window.stack.animations_enabled = False
        main_window.quick_script_subaction.trigger()
        main_window.button_group.buttons()[0].click()
        main_window.stack.animations_enabled = True

    raise SystemExit(app.exec())
