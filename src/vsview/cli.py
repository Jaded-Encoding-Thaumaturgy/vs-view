import faulthandler
import sys
from logging import DEBUG, getLogger
from pathlib import Path
from signal import SIG_DFL, SIGINT, signal
from typing import Annotated

from PySide6.QtWidgets import QApplication
from typer import Argument, Option, Typer
from vsengine.loops import set_loop

from .app.main import MainWindow
from .app.plugins.manager import PluginManager
from .app.settings import SettingsManager, ShortcutManager
from .assets import load_fonts
from .logging import console, setup_logging
from .vsenv import QtEventLoop

logger = getLogger(__name__)

app = Typer(
    name="vsview",
    help="",
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    add_completion=False,
)

input_file_arg = Argument(
    help="Path to input file(s); video(s), image(s) or script(s).",
    metavar="INPUT",
    resolve_path=True,
)

verbose_opt = Option(
    "--verbose",
    "-v",
    count=True,
    help="Enable verbose output. Use multiple times for increased verbosity (-v, -vv, -vvv, ...).",
)


@app.command()
def vsview_cli(
    files: Annotated[list[Path] | None, input_file_arg] = None,
    verbose: Annotated[int, verbose_opt] = 0,
) -> None:
    # Enable faulthandler to get stack traces on segfaults
    faulthandler.enable(file=console.file)

    # -v -> DEBUG, -vv -> DEBUG - 1, -vvv -> DEBUG - 2, etc.
    setup_logging(level=DEBUG - max(0, verbose - 1) if verbose else None)

    # Set signal handler to default to allow Ctrl+C to work
    signal(SIGINT, SIG_DFL)
    set_loop(QtEventLoop())
    SettingsManager()
    ShortcutManager()

    app = QApplication(sys.argv)
    PluginManager.load()
    load_fonts()

    main_window = MainWindow()
    main_window.ensurePolished()

    if files:
        main_window.show()
        for file in files:
            if file.suffix in [".py", ".vpy"]:
                main_window.load_new_script(file)
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

    sys.exit(app.exec())
