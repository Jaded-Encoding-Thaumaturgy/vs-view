from __future__ import annotations

import os
import shlex
import sys
from itertools import chain
from logging import DEBUG, getLogger
from signal import SIG_DFL, SIGINT, signal
from typing import Any

from .app.main import Application, MainWindow
from .app.plugins.manager import PluginManager
from .app.settings.models import GlobalSettings
from .app.workspace import BaseWorkspace, PythonScriptWorkspace, QuickScriptWorkspace, VideoFileWorkspace
from .assets import load_fonts
from .cli import IS_GUI_MODE, LOG_PATH
from .config import CLIConfig
from .env import getenv_bool, load_dotenv
from .logging import console, setup_logging

logger = getLogger(__name__)


def main(raw: dict[str, Any]) -> None:
    if not getenv_bool("VSVIEW_NO_DOTENV", False):
        load_dotenv()

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
    # Fixes the window flicker (https://qt-project.atlassian.net/browse/QTBUG-136743).
    os.environ.setdefault("QT_WIDGETS_RHI", "1")
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
    setup_logging(
        level=DEBUG - max(0, cfg.verbose - 1) if cfg.verbose else None,
        vs_level=cfg.vapoursynth_log_level,
        vsengine_level=cfg.vsengine_log_level,
        qt_level=cfg.qt_log_level,
        log_file=LOG_PATH if cfg.file_log or IS_GUI_MODE else None,
        is_gui_mode=IS_GUI_MODE,
    )

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
    elif cfg.workspace:
        PluginManager.wait_for_loaded()
        app.processEvents()

        workspaces: list[type[BaseWorkspace]] = [
            PythonScriptWorkspace,
            VideoFileWorkspace,
            QuickScriptWorkspace,
            *PluginManager.workspaces,
        ]
        possibles = {w.title.lower().replace(" ", "-"): w for w in workspaces}
        should_exit = False

        with main_window.stack.disable_animation():
            for choice in cfg.workspace:
                if choice not in possibles:
                    logger.critical("The %r workspace doesn't exist. Pick from %s", choice, list(possibles))
                    should_exit = True
                    continue
                main_window.add_workspace(possibles[choice])

        if should_exit:
            raise SystemExit(app.exit(1))

    elif not cfg.no_default_workspace:
        app.processEvents()
        # Now create default workspaces
        with main_window.stack.disable_animation():
            main_window.script_subaction.trigger()
            main_window.file_subaction.trigger()
            main_window.quick_script_subaction.trigger()
            main_window.button_group.buttons()[0].click()

    raise SystemExit(app.exec())
