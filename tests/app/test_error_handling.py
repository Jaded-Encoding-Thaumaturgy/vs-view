from __future__ import annotations

import logging
from pathlib import Path
from traceback import StackSummary, TracebackException
from unittest.mock import MagicMock, patch

import pytest
from vsengine.vpy import ExecutionError
from vsremote import RemoteExecutionError
from vsremote.protocol import RemoteErrorPayload, StackFrame, StatusCode

from vsview.app.error import (
    ErrorLocationInfo,
    extract_source_context,
    is_user_script_frame,
    resolve_error_location,
    show_error,
)

pytestmark = [pytest.mark.unit]


def test_is_user_script_frame() -> None:
    assert is_user_script_frame("D:/scripts/myscript.vpy") is True
    assert is_user_script_frame("C:/.venv/Lib/site-packages/vsengine/vpy.py") is False
    assert is_user_script_frame("D:/project/site-packages/plugin.py") is False
    assert is_user_script_frame("D:/custom/path.vpy", user_script_path="D:/custom/path.vpy") is True
    assert is_user_script_frame("vapoursynth.pyx") is False
    assert is_user_script_frame("src/cython/vapoursynth.pyx") is False
    assert is_user_script_frame("<frozen importlib._bootstrap>") is False
    assert is_user_script_frame("<editor>", user_script_path="<editor>") is True


def test_extract_source_context_fallback() -> None:
    lines = extract_source_context(
        filename="nonexistent.vpy",
        lineno=10,
        fallback_code_line="raise ValueError('fail')",
    )
    assert lines == ["File: nonexistent.vpy:10", ">  10 | raise ValueError('fail')"]


def test_resolve_remote_error_location() -> None:
    payload = RemoteErrorPayload(
        error="ValueError: something broke",
        exc_type="ValueError",
        exc_msg="something broke",
        filename="D:/test.vpy",
        lineno=15,
        code_line="raise ValueError('something broke')",
        formatted_traceback="Traceback:\n  File 'D:/test.vpy', line 15\nValueError: something broke\n",
        frames=[
            StackFrame(filename="C:/site-packages/vsengine/vpy.py", lineno=100, func_name="_execute"),
            StackFrame(filename="D:/test.vpy", lineno=15, code="raise ValueError('something broke')"),
        ],
    )
    err = RemoteExecutionError("Failed to load code", status=StatusCode.ERROR, payload=payload)

    info = resolve_error_location(err, user_script_path="D:/test.vpy")
    assert isinstance(info, ErrorLocationInfo)
    assert info.filename == "D:/test.vpy"
    assert info.lineno == 15
    assert info.exc_type == "ValueError"
    assert info.exc_msg == "something broke"
    assert info.code_line == "raise ValueError('something broke')"


def test_show_error_remote_execution(caplog: pytest.LogCaptureFixture) -> None:
    payload = RemoteErrorPayload(
        error="ValueError: something broke",
        exc_type="ValueError",
        exc_msg="something broke",
        filename="D:/test.vpy",
        lineno=15,
        code_line="raise ValueError('something broke')",
        formatted_traceback="Traceback:\n  File 'D:/test.vpy', line 15\nValueError: something broke\n",
    )
    err = RemoteExecutionError("Failed to load code", status=StatusCode.ERROR, payload=payload)

    parent = MagicMock()
    with (
        patch("vsview.app.error.display_error_dialog", return_value=0) as mock_dialog,
        caplog.at_level(logging.ERROR),
    ):
        show_error(err, parent, user_script_path="D:/test.vpy", header_suffix=" on the remote server")

        mock_dialog.assert_called_once()
        dialog_msg = mock_dialog.call_args[0][1]
        assert "A ValueError exception was raised while running the script on the remote server." in dialog_msg
        assert "File: D:/test.vpy:15" in dialog_msg
        assert ">  15 | raise ValueError('something broke')" in dialog_msg
        assert "ValueError: something broke" in dialog_msg

        # Check console log
        assert any(
            "A ValueError exception was raised while running the script on the remote server." in rec.message
            and "Full traceback:" in rec.message
            for rec in caplog.records
        )


def test_resolve_execution_error_with_cython_frame() -> None:
    class DummyError(Exception):
        pass

    exc = DummyError("Expr: argument clips was passed an unsupported type")

    tb_mock = MagicMock(spec=TracebackException)
    tb_mock.stack = StackSummary.from_list(
        [
            ("C:/site-packages/vsengine/vpy.py", 369, "_execute", "exec(code, module.__dict__)"),
            ("C:/gnosia/gnosia01.py", 152, "<module>", "filtering()"),
            (
                "C:/gnosia/gnosia01.py",
                81,
                "filtering",
                "insane_db_mask = core.std.Expr([insane_db_mask, edgemask3, None], 'x y -')",
            ),
            ("vapoursynth.pyx", 3543, "__call__", None),
        ]
    )

    err = ExecutionError(exc)

    with patch("vsview.app.error.TracebackException.from_exception", return_value=tb_mock):
        info = resolve_error_location(err, user_script_path="C:/gnosia/gnosia01.py")

    assert isinstance(info, ErrorLocationInfo)
    assert info.filename == "C:/gnosia/gnosia01.py"
    assert info.lineno == 81
    assert info.exc_type == "DummyError"
    assert info.exc_msg == "Expr: argument clips was passed an unsupported type"
    assert info.code_line == "insane_db_mask = core.std.Expr([insane_db_mask, edgemask3, None], 'x y -')"


def test_show_error_execution_error_cython_frame(tmp_path: Path) -> None:
    script_file = tmp_path / "gnosia01.py"
    script_file.write_text(
        "def filtering():\n"
        "    insane_db_mask = core.std.Expr([insane_db_mask, edgemask3, None], 'x y -')\n"
        "filtering()\n",
        encoding="utf-8",
    )

    class DummyError(Exception):
        pass

    exc = DummyError(
        "Expr: argument clips was passed an unsupported type (expected vnode[] compatible type but got NoneType)"
    )

    tb_mock = MagicMock(spec=TracebackException)
    tb_mock.stack = StackSummary.from_list(
        [
            ("C:/site-packages/vsengine/vpy.py", 369, "_execute", "exec(code)"),
            (
                str(script_file),
                2,
                "filtering",
                "insane_db_mask = core.std.Expr([insane_db_mask, edgemask3, None], 'x y -')",
            ),
            ("vapoursynth.pyx", 3543, "__call__", None),
        ]
    )

    err = ExecutionError(exc)
    parent = MagicMock()

    with (
        patch("vsview.app.error.TracebackException.from_exception", return_value=tb_mock),
        patch("vsview.app.error.display_error_dialog", return_value=0) as mock_dialog,
    ):
        show_error(err, parent, user_script_path=str(script_file))

        mock_dialog.assert_called_once()
        dialog_msg = mock_dialog.call_args[0][1]
        assert "A DummyError exception was raised while running the script." in dialog_msg
        assert f"File: {script_file}:2" in dialog_msg
        assert ">  2 |     insane_db_mask = core.std.Expr([insane_db_mask, edgemask3, None], 'x y -')" in dialog_msg
        assert "Expr: argument clips was passed an unsupported type" in dialog_msg
        assert "vapoursynth.pyx" not in dialog_msg


def test_resolve_remote_error_location_with_cython_frame() -> None:
    payload = RemoteErrorPayload(
        error="DummyError: invalid argument",
        exc_type="DummyError",
        exc_msg="invalid argument",
        filename="vapoursynth.pyx",
        lineno=3543,
        code_line=None,
        formatted_traceback="Traceback:\n...",
        frames=[
            StackFrame(filename="C:/site-packages/vsengine/vpy.py", lineno=369, func_name="_execute"),
            StackFrame(filename="D:/test.vpy", lineno=81, code="insane_db_mask = core.std.Expr([None], 'x y -')"),
            StackFrame(filename="vapoursynth.pyx", lineno=3543, func_name="__call__"),
        ],
    )
    err = RemoteExecutionError("Failed to load code", status=StatusCode.ERROR, payload=payload)

    info = resolve_error_location(err, user_script_path="D:/test.vpy")
    assert isinstance(info, ErrorLocationInfo)
    assert info.filename == "D:/test.vpy"
    assert info.lineno == 81
    assert info.code_line == "insane_db_mask = core.std.Expr([None], 'x y -')"
