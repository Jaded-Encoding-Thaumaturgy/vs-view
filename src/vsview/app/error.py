import linecache
from collections.abc import Sequence
from logging import getLogger
from pathlib import Path
from traceback import TracebackException
from typing import Any, NamedTuple, Protocol, runtime_checkable

from PySide6.QtGui import QFontMetrics, Qt
from PySide6.QtWidgets import QGridLayout, QMessageBox, QSpacerItem, QStyle, QWidget
from vsengine.vpy import ExecutionError

from ..assets import get_monospace_font
from ..vsenv import run_in_loop

logger = getLogger(__name__)


@runtime_checkable
class StackFrame(Protocol):
    filename: str
    lineno: int | None
    func_name: str | None = None
    code: str | None = None


@runtime_checkable
class ExceptionLike(Protocol):
    exc_type: str
    exc_msg: str
    filename: str | None
    lineno: int | None
    code_line: str | None = None
    formatted_traceback: str | None = None
    frames: Sequence[StackFrame] | None = None


class ErrorLocationInfo(NamedTuple):
    filename: str | None
    lineno: int | None
    exc_type: str
    exc_msg: str
    code_line: str | None = None


def is_user_script_frame(
    filename: str,
    user_script_path: str | None = None,
    prefix_filenames: tuple[str, ...] = ("src/cython/", "<", "vapoursynth.pyx"),
    markers: tuple[str, ...] = ("site-packages/", "/lib/", "vsengine/", "vsview/"),
) -> bool:
    normalized_filename = filename.lower().replace("\\", "/")

    if user_script_path:
        normalized_script = user_script_path.lower().replace("\\", "/")

        if (normalized_filename == normalized_script) or (
            user_script_path.startswith("<") and filename == user_script_path
        ):
            return True

    if filename.startswith(prefix_filenames) or normalized_filename.startswith(prefix_filenames):
        return False

    return not any(marker in normalized_filename for marker in markers)


def find_user_script_frame(
    tb: TracebackException, user_script_path: str | None = None
) -> tuple[str, int, str | None] | None:
    if not tb.stack:
        return None

    # Walk backwards from the last frame to find a user script frame
    for frame in reversed(tb.stack):
        if frame.filename and frame.lineno is not None and is_user_script_frame(frame.filename, user_script_path):
            code_line = frame.line.strip() if frame.line else None
            return (frame.filename, frame.lineno, code_line)

    return None


def extract_source_context(
    filename: str | None,
    lineno: int | None,
    radius: int = 3,
    fallback_code_line: str | None = None,
) -> list[str]:
    if not filename and lineno is None:
        return ["(no traceback information available)"]

    display_name = filename or "<script>"

    # Try reading from real file first, then fall back to linecache (for virtual files)
    lines = list[str]()
    if filename and not filename.startswith("<") and (p := Path(filename)).exists() and p.is_file():
        lines = p.read_text(errors="replace").splitlines()
    elif filename:
        lines = [line.rstrip("\n\r") for line in linecache.getlines(filename)]

    if not lines or lineno is None:
        if lineno is not None and fallback_code_line:
            return [f"File: {display_name}:{lineno}", f"> {lineno:3d} | {fallback_code_line}"]
        if lineno is not None:
            return [f"File: {display_name}:{lineno}", "(source code not available)"]
        return [f"File: {display_name}", "(no line number available)"]

    context = [f"File: {display_name}:{lineno}"]
    for i in range(max(0, lineno - radius - 1), min(len(lines), lineno + radius)):
        prefix = ">" if i + 1 == lineno else " "
        context.append(f"{prefix}{i + 1:3d} | {lines[i]}")

    return context


def resolve_error_location(
    error: ExecutionError | BaseException | ExceptionLike | Any,
    user_script_path: str | None = None,
) -> ErrorLocationInfo:
    if isinstance(error, ExecutionError):
        e = error.parent_error
        tb = TracebackException.from_exception(e)

        if isinstance(e, SyntaxError) and e.filename is not None and e.lineno is not None:
            filename, lineno = e.filename, e.lineno
            code_line = e.text.strip() if e.text else None
        elif result := find_user_script_frame(tb, user_script_path):
            filename, lineno, code_line = result
        else:
            filename, lineno, code_line = user_script_path, None, None

        return ErrorLocationInfo(filename or user_script_path, lineno, e.__class__.__name__, str(e), code_line)

    if isinstance(error, ExceptionLike):
        filename = error.filename
        lineno = error.lineno
        code_line = error.code_line

        if error.frames:
            for f in reversed(error.frames):
                fn = f.filename
                ln = f.lineno
                cl = f.code
                if fn and ln and is_user_script_frame(fn, user_script_path):
                    filename = fn
                    lineno = ln
                    if cl:
                        code_line = cl
                    break

        return ErrorLocationInfo(filename or user_script_path, lineno, error.exc_type, error.exc_msg, code_line)

    if isinstance(error, SyntaxError) and error.filename is not None and error.lineno is not None:
        code_line = error.text.strip() if error.text else None
        return ErrorLocationInfo(error.filename, error.lineno, "SyntaxError", error.msg or str(error), code_line)

    if isinstance(error, BaseException):
        tb = TracebackException.from_exception(error)
        result = find_user_script_frame(tb, user_script_path)
        if result:
            filename, lineno, code_line = result
        else:
            filename, lineno, code_line = user_script_path, None, None
        return ErrorLocationInfo(filename or user_script_path, lineno, error.__class__.__name__, str(error), code_line)

    return ErrorLocationInfo(user_script_path, None, getattr(error, "__class__", type(error)).__name__, str(error))


@run_in_loop(return_future=False)
def display_error_dialog(parent: QWidget, message: str, title: str = "Error") -> int:
    font = get_monospace_font()
    metrics = QFontMetrics(font)

    max_width = max((metrics.horizontalAdvance(line) for line in message.splitlines()), default=300)

    msg = QMessageBox(parent)
    msg.setIconPixmap(msg.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical).pixmap(48, 48))
    msg.setWindowTitle(title)
    msg.setText(message)
    msg.setFont(font)
    msg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    if isinstance(layout := msg.layout(), QGridLayout):
        spacer = QSpacerItem(max_width, 0)
        layout.addItem(spacer, layout.rowCount(), 0, 1, layout.columnCount())

    return msg.exec()


def show_error(
    error: ExecutionError | BaseException | ExceptionLike,
    parent: QWidget,
    user_script_path: str | None = None,
    header_suffix: str = "",
    title: str = "Error",
) -> int:
    """
    Format and display an error dialog for script execution failures.
    """
    info = resolve_error_location(error, user_script_path)
    context = extract_source_context(info.filename, info.lineno, fallback_code_line=info.code_line)

    header = f"A {info.exc_type} exception was raised while running the script{header_suffix}."
    detail = f"{info.exc_type}: {info.exc_msg}" if info.exc_msg else info.exc_type
    error_message = f"{header}\n\n{'\n'.join(context)}\n\n{detail}\n"

    if isinstance(error, ExecutionError):
        e: BaseException = getattr(error, "parent_error", error)
        logger.error("%s\n\nFull traceback:", error_message.strip(), exc_info=e)
    elif isinstance(error, ExceptionLike) and error.formatted_traceback:
        logger.error("%s\n\nFull traceback:\n%s", error_message.strip(), error.formatted_traceback.rstrip())
    elif isinstance(error, BaseException):
        logger.error("%s", error_message.strip(), exc_info=error)
    else:
        logger.error("%s", error_message.strip())

    return display_error_dialog(parent, error_message, title)
