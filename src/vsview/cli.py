from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping, Sequence
from itertools import chain
from logging import DEBUG, getLevelNamesMapping, getLogger
from pathlib import Path
from signal import SIG_DFL, SIGINT, signal
from typing import TYPE_CHECKING, Annotated, Any, cast, override

from cyclopts import App, Group, Parameter, Token, config, validators
from cyclopts.help import DefaultFormatter, HelpPanel, InlineText
from cyclopts.help.silent import SILENT
from cyclopts.help.specs import DefaultStyled, TableSpec, get_default_command_columns, get_default_parameter_columns

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType

from .env import getenv_bool
from .logging import IS_GUI_MODE, LOG_PATH, console, init_early_logging, setup_logging

logger = getLogger(__name__)


class CompactHelpFormatter(DefaultFormatter):
    @override
    def render_usage(self, console: Console, options: ConsoleOptions, usage: Any) -> None:
        from rich.text import Text

        self._deferred_usage = Text("Usage: ", style="cyclopts.usage") + Text(str(usage), "cyan")

    @override
    def render_description(self, console: Console, options: ConsoleOptions, description: InlineText) -> None:
        from rich.markdown import Markdown

        description.primary_renderable = cast(Markdown, description.primary_renderable).markup.replace("\n\n", "\n")
        super().render_description(console, options, description)
        super().render_usage(console, options, self._deferred_usage)

    @override
    def _render_panel(self, help_panel: HelpPanel, console: Console, options: ConsoleOptions) -> RenderableType:
        if not help_panel.entries:
            return SILENT

        from rich.console import Group as RichGroup
        from rich.padding import Padding
        from rich.text import Text

        # Use Cyclopts's standard column renderers (names, descriptions, metavars)
        columns = self.column_specs
        if columns is None:
            columns = get_default_command_columns if help_panel.format == "command" else get_default_parameter_columns
        if callable(columns):
            columns = columns(console, options, help_panel.entries)

        table = (self.table_spec or TableSpec(padding=(0, 2, 0, 0))).build(columns, help_panel.entries)

        # Clean title header instead of a border panel
        renderables: list[RenderableType] = [Text(f"{help_panel.title}:", style="bold green")]

        if help_panel.description:
            renderables.append(help_panel.description)

        # 2 spaces left indentation for entries
        renderables.append(Padding(table, (0, 0, 1, 2)))

        # DefaultStyled resolves the `cyclopts.*` theme styles and group-level themes
        return DefaultStyled(RichGroup(*renderables), theme=help_panel.theme)


options_group = Group("Options", sort_key=10)
app = App(
    "vsview",
    console=console,
    help_formatter=CompactHelpFormatter(),
    default_parameter=Parameter(negative_iterable=(), negative_bool=(), group=options_group, show_default=False),
    config=[config.Env("VSVIEW_", command=False, show=False)],
)
settings_app = app.command(App(name="settings", help="Settings management", sort_key=10))
workspace_group = Group("Workspace Options", sort_key=20)
settings_group = Group("Settings Options", sort_key=30)
logging_group = Group("Logging Options", sort_key=40)
app["--help"].group = "Options"
app["--help"].help = "Print help."
app["--version"].group = "Options"
app["--version"].help = "Print version."
app["--version"].alias = "-V"


all_log_levels = tuple(ll.lower() for ll in getLevelNamesMapping())


def convert_log_level(type_: str, tokens: list[Token]) -> int:
    return getLevelNamesMapping()[tokens[0].value.upper()]


def convert_arg(type_: type[Mapping[str, str]], tokens: list[Token]) -> dict[str, str]:
    d = dict[str, str]()
    for arg in tokens:
        k, v = arg.value.split("=", 1)
        d[k] = v
    return d


def convert_qt_args(type_: type[Sequence[str]], tokens: list[Token]) -> list[str]:
    return list(chain.from_iterable(shlex.split(t.value) for t in tokens))


@app.meta.default
def main_meta(*tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)]) -> None:
    init_early_logging()

    # Manually add scripts folder to the PATH when embebbed with PyApp
    if getenv_bool("PYAPP"):
        _populate_path()

    if getenv_bool("VSVIEW_NO_DOTENV", False):
        app.config = None

    app(tokens)


@app.default
def main(
    *files: Annotated[
        Path,
        Parameter("", validator=validators.Path(exists=True, dir_okay=False), group="Arguments", metavar="[FILE]..."),
    ],
    verbose: Annotated[int, Parameter(alias="-v", count=True)] = 0,
    arg: Annotated[
        Mapping[str, str],
        Parameter(alias="-a", converter=convert_arg, allow_repeating=True, n_tokens=-1, metavar="<KEY=VALUE>"),
    ] = {},
    qt_arg: Annotated[
        Sequence[str],
        Parameter(alias="-q", converter=convert_qt_args, allow_leading_hyphen=True, metavar="<ARG>"),
    ] = (),
    hdr: Annotated[bool, Parameter(env_var="VSVIEW_HDR")] = False,
    workspace: Annotated[Sequence[str], Parameter(alias="-w", group=workspace_group, metavar="")] = (),
    no_default_workspace: Annotated[
        bool,
        Parameter(group=workspace_group, env_var="VSVIEW_NO_DEFAULT_WORKSPACE"),
    ] = False,
    no_settings: Annotated[bool, Parameter(group=settings_group, env_var="VSVIEW_NO_SETTINGS")] = False,
    settings_roaming: Annotated[
        bool,
        Parameter(group=settings_group, env_var="VSVIEW_GLOBAL_SETTINGS_ROAMING"),
    ] = False,
    settings_env: Annotated[
        bool,
        Parameter(group=settings_group, env_var="VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT"),
    ] = False,
    settings_env_copy: Annotated[
        bool,
        Parameter(group=settings_group, env_var="VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"),
    ] = False,
    file_log: Annotated[bool, Parameter(group=logging_group, env_var="VSVIEW_FILE_LOG")] = False,
    vapoursynth_log_level: Annotated[
        int | None,
        Parameter(
            alias="-vs-ll",
            group=logging_group,
            choices=all_log_levels,
            env_var="VSVIEW_VS_LOG_LEVEL",
            converter=convert_log_level,
            metavar="<LEVEL>",
        ),
    ] = None,
    vsengine_log_level: Annotated[
        int | None,
        Parameter(
            alias="-vse-ll",
            group=logging_group,
            choices=all_log_levels,
            env_var="VSVIEW_VSENGINE_LOG_LEVEL",
            converter=convert_log_level,
            metavar="<LEVEL>",
        ),
    ] = None,
    qt_log_level: Annotated[
        int | None,
        Parameter(
            alias="-qt-ll",
            group=logging_group,
            choices=all_log_levels,
            env_var="VSVIEW_QT_LOG_LEVEL",
            converter=convert_log_level,
            metavar="<LEVEL>",
        ),
    ] = None,
) -> None:
    """
    Preview VapourSynth scripts, videos, images and audio in a desktop viewer.

    Open one or more input files directly, or start without files to open the default workspaces.

    Args:
        files: Path to input file(s); video(s), image(s) or script(s).
        verbose: Enable verbose output. Repeat to increase verbosity (-v, -vv, -vvv, ...).
        arg: Argument passed to the script environment. Can be specified multiple times.
        qt_arg: Pass an argument directly to the underlying Qt application.
        hdr: Enable High Dynamic Range (HDR) support and set up graphics APIs.
        workspace: Open a specific workspace on startup. Can be specified multiple times to open several at once.
        no_default_workspace: Start the app without opening any workspace.
        no_settings: Run without loading or saving any settings for this session.
        settings_roaming: Store global settings in %APPDATA% instead of %LOCALAPPDATA% (Windows only).
        settings_env: Scope global settings to the active Python environment to prevent conflicts
        settings_env_copy: If **--settings-env** is set, and the scoped file doesn't exist yet,
            seed it from the base **global_settings.json**.
        file_log: Enable file logging in the platform's standard log directory.
        vapoursynth_log_level: VapourSynth log level.
        vsengine_log_level: VSEngine log level.
        qt_log_level: Qt log level.
    """
    from .app.main import Application, MainWindow
    from .app.plugins.manager import PluginManager
    from .app.workspace import BaseWorkspace, PythonScriptWorkspace, QuickScriptWorkspace, VideoFileWorkspace
    from .assets import load_fonts

    # Setup env vars
    os.environ["JETPYTOOLS_NO_COLOR"] = "true"
    os.environ["PYDANTIC_ERRORS_INCLUDE_URL"] = "false"
    # Fixes the window flicker (https://qt-project.atlassian.net/browse/QTBUG-136743).
    os.environ.setdefault("QT_WIDGETS_RHI", "1")
    if hdr:
        os.environ.setdefault("QSG_RHI_HDR", "p3" if sys.platform == "darwin" else "scrgb")
        os.environ.setdefault("QSG_INFO", "1")
        os.environ.setdefault("QSG_RHI_DEBUG_LAYER", "1")
        os.environ.setdefault("QSG_RHI_LEAK_CHECK", "1")
        os.environ.setdefault("QSG_RHI_PROFILE", "1")
        if sys.platform == "linux":
            os.environ.setdefault("QSG_RENDER_LOOP", "basic")

    if settings_roaming:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ROAMING"] = "true"
    if settings_env:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT"] = "true"
    if settings_env_copy:
        os.environ["VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY"] = "true"
    if hdr:
        os.environ["VSVIEW_HDR"] = "true"

    # -v -> DEBUG, -vv -> DEBUG - 1, -vvv -> DEBUG - 2, etc.
    setup_logging(
        level=DEBUG - max(0, verbose - 1) if verbose else None,
        vs_level=vapoursynth_log_level,
        vsengine_level=vsengine_log_level,
        qt_level=qt_log_level,
        log_file=LOG_PATH if file_log or IS_GUI_MODE else None,
        is_gui_mode=IS_GUI_MODE,
    )

    # Set signal handler to default to allow Ctrl+C to work
    signal(SIGINT, SIG_DFL)

    if hdr:
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

        match sys.platform:
            case "win32":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Direct3D12)
            case "linux":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Vulkan)
            case "darwin":
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Metal)

    app = Application(
        [sys.argv[0], *chain.from_iterable(shlex.split(q) for q in qt_arg)],
        no_settings=no_settings,
    )

    PluginManager.load()
    load_fonts()

    main_window = MainWindow()
    # Show window first for faster perceived startup
    main_window.show()

    if files:
        for file in files:
            if file.suffix in [".py", ".vpy"]:
                main_window.load_new_script(file, **arg)
            else:
                main_window.load_new_file(file)
    elif workspace:
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
            for choice in workspace:
                if choice not in possibles:
                    logger.critical("The %r workspace doesn't exist. Pick from %s", choice, list(possibles))
                    should_exit = True
                    continue
                main_window.add_workspace(possibles[choice])

        if should_exit:
            raise SystemExit(app.exit(1))

    elif not no_default_workspace:
        # Run VSView Editor as default workspace when launched from pythonw and PYAPP environment variable
        if IS_GUI_MODE and getenv_bool("PYAPP"):
            PluginManager.wait_for_loaded()
            app.processEvents()

            for wk in PluginManager.workspaces:
                if wk.identifier == "jet_vsview_editor":
                    main_window.add_workspace(wk)
                    break
            else:
                logger.critical("The VSView Editor workspace doesn't exist. Broken installation!")
                raise SystemExit(app.exit(1))
        else:
            # Now create default workspaces
            app.processEvents()
            with main_window.stack.disable_animation():
                main_window.script_subaction.trigger()
                main_window.file_subaction.trigger()
                main_window.quick_script_subaction.trigger()
                main_window.button_group.buttons()[0].click()

    raise SystemExit(app.exec())


@app.command(sort_key=100)
def version() -> None:
    """Show the installed vsview version and exit."""
    app.version_print()


@app.command(sort_key=100)
def help(*command: Annotated[str, Parameter(show=False)]) -> None:
    """Print this message or the help of the given subcommand(s)"""
    app.help_print(command)


@settings_app.command(name="path")
def settings_path() -> None:
    """
    Print to stdout the resolved global_settings.json path and exit.

    The resolved path respects environment scoping if **--settings-env** is active.

    Default base directory is:

    - %LOCALAPPDATA%\\vsview\\ on Windows,
    - ~/.config/vsview/ on Linux
    - ~/Library/Application Support/vsview/ on macOS.
    """
    from .app.settings.models import GlobalSettings

    console.print(GlobalSettings.path_env)


@settings_app.command(name="wipe")
def settings_wipe(*, all: bool = False) -> None:
    """Delete the **global_settings.json** file (as shown by **vsview settings path**) and exit."""
    from .app.settings.models import GlobalSettings

    if GlobalSettings.path_env.exists():
        GlobalSettings.path_env.unlink()
        console.print("Global config file successfully deleted.")
    else:
        console.print("No global config file found.")

    if all:
        if GlobalSettings.config_path.exists():
            GlobalSettings.config_path.rmdirs(ignore_errors=True)
            console.print("Global config path successfully deleted.")
        else:
            console.print("No global config path found.")


@settings_app.command(name="help")
def settings_help(*command: Annotated[str, Parameter(show=False)]) -> None:
    """Print this message or the help of the given subcommand(s)"""
    settings_app.help_print(command)


def _populate_path() -> None:
    py_bin = Path(sys.executable).parent
    path_parts = os.environ.get("PATH", "").split(os.pathsep)

    for folder in [py_bin, py_bin / "Scripts"] if sys.platform == "win32" else [py_bin]:
        if folder.is_dir() and str(folder.resolve()) not in path_parts:
            path_parts.insert(0, str(folder))

    os.environ["PATH"] = os.pathsep.join(path_parts)
