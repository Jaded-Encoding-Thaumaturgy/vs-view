import linecache
from logging import getLogger
from pathlib import Path
from traceback import TracebackException

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QGridLayout, QMessageBox, QSpacerItem, QWidget
from vsengine.vpy import ExecutionError

from ..assets import get_monospace_font
from ..vsenv import run_in_loop

logger = getLogger(__name__)


@run_in_loop
def show_error(error: ExecutionError, parent: QWidget) -> None:
    e = error.parent_error
    tb = TracebackException.from_exception(e)
    context = list[str]()

    # For SyntaxError, get location from the error itself, not the traceback
    # (traceback points to vsengine's compile() call, not the user's script)
    if isinstance(e, SyntaxError) and e.filename is not None and e.lineno is not None:
        filename, lineno = e.filename, e.lineno
    elif tb.stack and (last_frame := tb.stack[-1]) and last_frame.filename and last_frame.lineno is not None:
        filename, lineno = last_frame.filename, last_frame.lineno
    else:
        filename, lineno = None, None

    if filename and lineno:
        # Try reading from real file first, then fall back to linecache (for virtual files)

        if not filename.startswith("<") and (p := Path(filename)).exists():
            lines = p.read_text().splitlines()
        else:
            lines = [line.rstrip("\n\r") for line in linecache.getlines(filename)]

        if lines:
            context.append(f"File: {filename}:{lineno}")

            for i in range(max(0, lineno - 4), min(len(lines), lineno + 3)):
                prefix = ">" if i + 1 == lineno else " "
                context.append(f"{prefix}{i + 1:3d} | {lines[i]}")
        else:
            context.append(f"File: {filename}:{lineno}")
            context.append("(source code not available)")
    else:
        context.append("(no traceback information available)")

    error_message = f"A {e.__class__.__name__} exception was raised while running the script.\n\n{'\n'.join(context)}\n"

    logger.error(error_message)

    font = get_monospace_font()
    metrics = QFontMetrics(font)

    max_width = max(metrics.horizontalAdvance(line) for line in error_message.splitlines())

    msg = QMessageBox(parent)
    msg.setIconPixmap(msg.style().standardIcon(msg.style().StandardPixmap.SP_MessageBoxCritical).pixmap(48, 48))
    msg.setWindowTitle("Error")
    msg.setText(error_message)
    msg.setFont(font)

    if isinstance(layout := msg.layout(), QGridLayout):
        spacer = QSpacerItem(max_width, 0)
        layout.addItem(spacer, layout.rowCount(), 0, 1, layout.columnCount())

    msg.exec()
