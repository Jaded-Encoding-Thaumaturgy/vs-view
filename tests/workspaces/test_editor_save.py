from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsview_editor.web import JsToPyCommand, MonacoEditorWidget, PyToJsCommand
from vsview_editor.workspace import EditorWorkspace

from vsview.app.plugins.manager import PluginManager
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.integration, pytest.mark.qt, pytest.mark.vpy("no-core")]

UNTITLED_URI = "file:///workspace/untitled_ab12.py"


@pytest.fixture
def editor_workspace(
    qtbot: QtBot,
    qt_event_loop: QtEventLoop,
    init_plugin_manager: PluginManager,
    mocker: MockerFixture,
) -> Generator[EditorWorkspace]:
    mocker.patch.object(MonacoEditorWidget, "load")
    workspace = EditorWorkspace()
    try:
        yield workspace
    finally:
        # Not registered with qtbot: closing a workspace with a dirty tab opens a real modal "Save Changes?" box.
        send_from_js(workspace, JsToPyCommand.TAB_STATE_CHANGED, {"tabs": []})
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def dispatch(editor_workspace: EditorWorkspace, mocker: MockerFixture) -> MagicMock:
    return mocker.patch.object(editor_workspace.code_dock.editor.bridge, "dispatch")


def send_from_js(workspace: EditorWorkspace, command: JsToPyCommand, payload: dict[str, Any]) -> None:
    workspace.code_dock.editor.bridge.dispatchFromJs(command, json.dumps(payload))


def open_dirty_tab(workspace: EditorWorkspace, uri: str, content: str) -> None:
    tab = {"uri": uri, "title": "script.vpy", "isMain": True, "isDirty": True, "language": "python"}
    send_from_js(workspace, JsToPyCommand.TAB_STATE_CHANGED, {"tabs": [{**tab, "content": content}]})
    send_from_js(workspace, JsToPyCommand.ACTIVE_TAB_CHANGED, {"uri": uri})
    send_from_js(workspace, JsToPyCommand.CONTENT_CHANGED, {"content": content})


def tab_saved(uri: str, old_uri: str) -> Any:
    return call(PyToJsCommand.TAB_SAVED, {"uri": uri, "oldUri": old_uri})


class TestConfirmCloseSave:
    def test_saves_dirty_file_backed_tab(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        qtbot: QtBot,
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        uri = script.resolve().as_uri()
        open_dirty_tab(editor_workspace, uri, "new content\n")
        mocker.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save)

        assert editor_workspace.confirm_close() is True
        assert script.read_bytes() == b"new content\n"
        qtbot.waitUntil(lambda: tab_saved(uri, uri) in dispatch.call_args_list, timeout=2000)

    def test_saves_dirty_untitled_tab(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        qtbot: QtBot,
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        target = tmp_path / "chosen.vpy"
        open_dirty_tab(editor_workspace, UNTITLED_URI, "untitled content\n")
        mocker.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save)
        mocker.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), ""))

        assert editor_workspace.confirm_close() is True
        assert target.read_bytes() == b"untitled content\n"
        qtbot.waitUntil(
            lambda: tab_saved(target.resolve().as_uri(), UNTITLED_URI) in dispatch.call_args_list, timeout=2000
        )

    def test_failed_save_keeps_workspace_open_and_warns(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        qtbot: QtBot,
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        target = tmp_path / "is_a_directory.vpy"
        target.mkdir()
        open_dirty_tab(editor_workspace, target.resolve().as_uri(), "precious unsaved buffer\n")
        mocker.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save)
        warning = mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok)

        with qtbot.waitSignal(editor_workspace.statusLoadingErrored, timeout=2000):
            closed = editor_workspace.confirm_close()

        assert closed is False
        warning.assert_called_once()
        assert warning.call_args.args[1] == "Save Failed"
        assert "script.vpy" in warning.call_args.args[2]
        assert not any(c.args[0] == PyToJsCommand.TAB_SAVED for c in dispatch.call_args_list)


class TestSaveScript:
    def test_success_reports_saved_status_and_tab_saved(
        self, editor_workspace: EditorWorkspace, dispatch: MagicMock, qtbot: QtBot, tmp_path: Path
    ) -> None:
        script = tmp_path / "script.vpy"
        resolved = script.resolve()

        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=2000) as blocker:
            saved_path = editor_workspace.code_dock.save_script(script, "x = 1\n").result(timeout=5)

        assert saved_path == resolved
        assert blocker.args == ["Saved"]
        assert script.read_bytes() == b"x = 1\n"
        assert tab_saved(resolved.as_uri(), resolved.as_uri()) in dispatch.call_args_list

    def test_failure_reports_error_status(
        self, editor_workspace: EditorWorkspace, dispatch: MagicMock, qtbot: QtBot, tmp_path: Path
    ) -> None:
        target = tmp_path / "is_a_directory.vpy"
        target.mkdir()

        with (
            qtbot.assertNotEmitted(editor_workspace.statusLoadingFinished, wait=100),
            qtbot.waitSignal(editor_workspace.statusLoadingErrored, timeout=2000) as blocker,
            pytest.raises(IsADirectoryError, match=r"Is a directory"),
        ):
            editor_workspace.code_dock.save_script(target, "x = 1\n").result(timeout=5)

        assert blocker.args == ["Error while saving script"]
        assert not any(c.args[0] == PyToJsCommand.TAB_SAVED for c in dispatch.call_args_list)

    def test_failed_write_keeps_the_old_file(self, editor_workspace: EditorWorkspace, tmp_path: Path) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old content\n")

        # A lone surrogate cannot be encoded to UTF-8, so the write fails after opening the temp file.
        with pytest.raises(UnicodeEncodeError, match=r"surrogates not allowed"):
            editor_workspace.code_dock.save_script(script, "new content\n\ud800").result(timeout=5)

        assert script.read_bytes() == b"old content\n"
        assert [p.name for p in tmp_path.iterdir()] == ["script.vpy"]

    def test_read_only_file_is_not_replaced(self, editor_workspace: EditorWorkspace, tmp_path: Path) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old content\n")
        script.chmod(stat.S_IREAD)

        try:
            with pytest.raises(PermissionError, match=r"File is read-only"):
                editor_workspace.code_dock.save_script(script, "new content\n").result(timeout=5)
        finally:
            script.chmod(stat.S_IREAD | stat.S_IWRITE)

        assert script.read_bytes() == b"old content\n"
        assert [p.name for p in tmp_path.iterdir()] == ["script.vpy"]

    def test_failed_save_as_preserves_active_filepath(
        self,
        editor_workspace: EditorWorkspace,
        qtbot: QtBot,
        tmp_path: Path,
        mocker: MockerFixture,
    ) -> None:
        target = tmp_path / "is_a_directory.vpy"
        target.mkdir()
        open_dirty_tab(editor_workspace, UNTITLED_URI, "x = 1\n")
        mocker.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), ""))

        with qtbot.waitSignal(editor_workspace.statusLoadingErrored, timeout=2000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_AS_REQUESTED, {})

        assert editor_workspace.code_dock.active_filepath is None


class TestLineEndings:
    @pytest.mark.parametrize(
        "content",
        [
            pytest.param("a = 1\nb = 2\n", id="lf"),
            pytest.param("a = 1\r\nb = 2\r\n", id="crlf"),
            pytest.param("a = 1\r\nb = 2\nc = 3\r\n", id="mixed"),
        ],
    )
    def test_save_writes_line_endings_verbatim(
        self, editor_workspace: EditorWorkspace, dispatch: MagicMock, tmp_path: Path, content: str
    ) -> None:
        script = tmp_path / "script.vpy"

        saved = editor_workspace.code_dock.save_script(script, content).result(timeout=5)

        assert saved == script.resolve()
        assert script.read_bytes() == content.encode()

    def test_open_file_keeps_crlf(self, editor_workspace: EditorWorkspace, dispatch: MagicMock, tmp_path: Path) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"a = 1\r\nb = 2\r\n")

        assert editor_workspace.code_dock.open_file(script) is True

        payload = dispatch.call_args_list[-1].args[1]
        assert dispatch.call_args_list[-1].args[0] == PyToJsCommand.OPEN_TAB
        assert payload["content"] == "a = 1\r\nb = 2\r\n"

    def test_read_file_keeps_crlf(self, editor_workspace: EditorWorkspace, tmp_path: Path) -> None:
        script = tmp_path / "module.py"
        script.write_bytes(b"a = 1\r\nb = 2\r\n")

        assert editor_workspace.code_dock.editor.bridge.readFile(script.as_uri()) == {
            "ok": True,
            "content": "a = 1\r\nb = 2\r\n",
        }

    def test_open_then_save_round_trips_bytes(
        self, editor_workspace: EditorWorkspace, dispatch: MagicMock, tmp_path: Path
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"a = 1\nb = 2\n")
        editor_workspace.code_dock.open_file(script)
        content = dispatch.call_args_list[-1].args[1]["content"]

        saved = editor_workspace.code_dock.save_script(script, content).result(timeout=5)

        assert saved == script.resolve()
        assert script.read_bytes() == b"a = 1\nb = 2\n"

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not on PATH")
    def test_format_keeps_crlf(self, editor_workspace: EditorWorkspace, dispatch: MagicMock, qtbot: QtBot) -> None:
        bridge = editor_workspace.code_dock.editor.bridge
        send_from_js(editor_workspace, JsToPyCommand.CONTENT_CHANGED, {"content": "a=1\r\nb=2\r\n"})

        send_from_js(editor_workspace, JsToPyCommand.FORMAT_REQUESTED, {})

        qtbot.waitUntil(lambda: editor_workspace.code_dock.format_btn.isEnabled(), timeout=10000)
        assert bridge.content == "a = 1\r\nb = 2\r\n"


def change_on_disk(path: Path, data: bytes, *, keep_mtime: bool = False) -> None:
    before = path.stat()
    path.write_bytes(data)
    if keep_mtime:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        # Coarse file system clocks could give the same mtime to both writes.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))


class TestDiskChangeWarning:
    def open_and_edit(self, workspace: EditorWorkspace, path: Path, content: str) -> None:
        assert workspace.code_dock.open_file(path) is True
        send_from_js(workspace, JsToPyCommand.CONTENT_CHANGED, {"content": content})

    @pytest.mark.parametrize(
        ("external", "keep_mtime"),
        [
            pytest.param(b"other\n", False, id="mtime"),
            pytest.param(b"other program\n", True, id="size"),
        ],
    )
    def test_cancel_keeps_the_external_change(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
        external: bytes,
        keep_mtime: bool,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        self.open_and_edit(editor_workspace, script, "mine\n")
        change_on_disk(script, external, keep_mtime=keep_mtime)
        warning = mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel)

        with qtbot.assertNotEmitted(editor_workspace.statusLoadingStarted, wait=100):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_called_once()
        assert warning.call_args.args[1] == "File Changed on Disk"
        assert "script.vpy" in warning.call_args.args[2]
        assert script.read_bytes() == external

    def test_confirm_overwrites_the_external_change(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        self.open_and_edit(editor_workspace, script, "mine\n")
        change_on_disk(script, b"other\n")
        warning = mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Save)

        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=5000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_called_once()
        assert script.read_bytes() == b"mine\n"

    def test_unchanged_file_saves_without_a_warning(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        self.open_and_edit(editor_workspace, script, "mine\n")
        warning = mocker.patch.object(QMessageBox, "warning")

        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=5000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_not_called()
        assert script.read_bytes() == b"mine\n"

    def test_own_save_is_the_new_reference(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        self.open_and_edit(editor_workspace, script, "first save, longer\n")
        warning = mocker.patch.object(QMessageBox, "warning")
        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=5000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})
        send_from_js(editor_workspace, JsToPyCommand.CONTENT_CHANGED, {"content": "second\n"})

        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=5000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_not_called()
        assert script.read_bytes() == b"second\n"

    def test_file_deleted_on_disk_is_saved_without_a_warning(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        self.open_and_edit(editor_workspace, script, "mine\n")
        script.unlink()
        warning = mocker.patch.object(QMessageBox, "warning")

        with qtbot.waitSignal(editor_workspace.statusLoadingFinished, timeout=5000):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_not_called()
        assert script.read_bytes() == b"mine\n"

    def test_reopening_a_dirty_tab_does_not_hide_the_external_change(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        qtbot: QtBot,
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        assert editor_workspace.code_dock.open_file(script) is True
        open_dirty_tab(editor_workspace, script.resolve().as_uri(), "mine\n")
        change_on_disk(script, b"other\n")
        assert editor_workspace.code_dock.open_file(script) is True
        warning = mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel)

        with qtbot.assertNotEmitted(editor_workspace.statusLoadingStarted, wait=100):
            send_from_js(editor_workspace, JsToPyCommand.SAVE_REQUESTED, {})

        warning.assert_called_once()
        assert script.read_bytes() == b"other\n"

    def test_close_is_cancelled_when_the_overwrite_is_refused(
        self,
        editor_workspace: EditorWorkspace,
        dispatch: MagicMock,
        tmp_path: Path,
        mocker: MockerFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        script = tmp_path / "script.vpy"
        script.write_bytes(b"old\n")
        assert editor_workspace.code_dock.open_file(script) is True
        uri = script.resolve().as_uri()
        open_dirty_tab(editor_workspace, uri, "mine\n")
        change_on_disk(script, b"other\n")
        mocker.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save)
        warning = mocker.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel)

        closed = editor_workspace.confirm_close()
        assert closed is False
        warning.assert_called_once()
        assert warning.call_args.args[1] == "File Changed on Disk"
        assert script.read_bytes() == b"other\n"
