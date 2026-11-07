from __future__ import annotations

import json
import sys
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from jetpytools import CustomValueError
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QObject, QPointF, QProcess, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsview_editor.console import IN_LOGGING, ConsoleStreamRedirector, GlobalConsoleHub
from vsview_editor.lsp import LSPConfig, LSPProcessManager, LSPProcessServer
from vsview_editor.utils import ContentPath, WorkspaceUri, extract_supported_script_files
from vsview_editor.web import (
    ConsoleAppendPayload,
    JsToPyCommand,
    MonacoBridge,
    MonacoEditorWidget,
    PyToJsCommand,
    TabInfo,
)
from vsview_editor.workspace import EditorShortcut, EditorWorkspace, MonacoEditorDock, StackDock

pytestmark = [pytest.mark.integration, pytest.mark.qt]


class TestWorkspaceUri:
    def test_non_file_scheme_returns_none(self) -> None:
        assert WorkspaceUri("vscode-vfs://host/path/to/file.py").local_path is None
        assert WorkspaceUri("inmemory://model/1").local_path is None
        assert WorkspaceUri("untitled:Untitled-1").local_path is None

    def test_virtual_workspace_uris_return_none(self) -> None:
        assert WorkspaceUri("file:///workspace/script.py").local_path is None
        assert WorkspaceUri("file:///workspace/untitled_1234.py").local_path is None
        assert WorkspaceUri("file:///workspace/workspace.code-workspace").local_path is None

    def test_existing_local_file_detected(self, tmp_path: Path) -> None:
        real_file = tmp_path / "script.py"
        real_file.write_text("print('hello')", encoding="utf-8")

        uri = WorkspaceUri(real_file.as_uri())
        assert uri.local_path == real_file

    def test_nonexistent_local_file_with_valid_path(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "subfolder" / "not_created_yet.vpy"
        uri = WorkspaceUri(nonexistent.as_uri())
        assert uri.local_path == nonexistent

    def test_linux_workspace_folder_path_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        uri = WorkspaceUri("file:///workspace/my_project/filter.vpy")
        assert uri.local_path == Path("/workspace/my_project/filter.vpy")


class TestContentPath:
    def test_protocol_methods(self) -> None:
        code = "import vapoursynth as vs\ncore = vs.core\n"
        filename = "<editor virtual>"
        cp = ContentPath(code, filename)

        assert cp.code == code
        assert cp.filename == filename
        assert cp.__fspath__() == filename
        assert len(cp) == len(code)
        assert str(cp) == code
        assert repr(cp) == filename

    def test_splitlines(self) -> None:
        cp = ContentPath("line1\nline2\r\nline3", "test.py")
        assert cp.splitlines() == ["line1", "line2", "line3"]
        assert cp.splitlines(keepends=True) == ["line1\n", "line2\r\n", "line3"]


class TestLSPConfigAndServer:
    def test_lsp_config_valid(self) -> None:
        config = LSPConfig(
            id="test_lsp",
            name="Test Language Server",
            command=["test-lsp", "--stdio"],
            language="python",
            file_events_pattern="**/*.py",
        )
        assert config.id == "test_lsp"
        assert config.language == "python"

    def test_lsp_config_empty_command_raises(self) -> None:
        with pytest.raises(CustomValueError, match=r"\(empty_lsp\) LSP config %r has an empty command list\."):
            LSPConfig(
                id="empty_lsp",
                name="Empty Server",
                command=[],
                language="python",
                file_events_pattern=None,
            )

    def test_build_uri_variations_windows(self) -> None:
        variations = LSPProcessServer._build_uri_variations(Path("C:/Users/test/workspace"))
        assert len(variations) == 18
        assert "file:///C:/Users/test/workspace" in variations
        assert "file:///C%3A/Users/test/workspace" in variations
        assert any("c:" in v.lower() or "c%3a" in v.lower() for v in variations)

    def test_build_uri_variations_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        posix_dir = Path("/home/developer/project")
        variations = LSPProcessServer._build_uri_variations(posix_dir)

        assert len(variations) == 2
        assert variations == ["file:///home/developer/project", "file:/home/developer/project"]

    def test_lsp_process_manager_restart_server(self, mocker: MockerFixture) -> None:
        parent = QObject()
        manager = LSPProcessManager(parent)

        mock_server = mocker.MagicMock(spec=LSPProcessServer)
        mock_server.start.return_value = 12345
        mocker.patch("vsview_editor.lsp.LSPProcessServer", return_value=mock_server)

        config = LSPConfig(
            id="test_lsp",
            name="Test Language Server",
            command=["test-lsp", "--stdio"],
            language="python",
            file_events_pattern="**/*.py",
        )

        port = manager.start_server(config)
        assert port == 12345
        assert config.id in manager.servers

        # Restart server
        mock_server.start.return_value = 54321
        new_port = manager.restart_server(config)
        assert new_port == 54321
        assert mock_server.stop.call_count >= 1

    def test_client_virtual_uri_translation(self, mocker: MockerFixture) -> None:
        config = LSPConfig(
            id="test_lsp",
            name="Test Server",
            command=["test-lsp", "--stdio"],
            language="python",
            file_events_pattern="**/*.py",
        )
        server = LSPProcessServer(config)
        server.real_uri = "file:///C:/Users/test/workspace"
        mock_proc = mocker.MagicMock()
        mock_proc.state.return_value = QProcess.ProcessState.Running
        server.process = mock_proc

        written_data: list[bytes] = []
        mock_proc.write.side_effect = lambda data: written_data.append(bytes(data))

        # Virtual files should be translated
        msg1 = '{"method":"textDocument/didOpen","params":{"textDocument":{"uri":"file:///workspace/script.py"}}}'
        server._on_ws_message(msg1)
        assert b"file:///C:/Users/test/workspace/script.py" in written_data[-1]

        # Non-virtual file under workspace (e.g. Linux path) should NOT be translated
        msg2 = '{"method":"textDocument/didOpen","params":{"textDocument":{"uri":"file:///workspace/my_project/filter.vpy"}}}'
        server._on_ws_message(msg2)
        assert b"file:///workspace/my_project/filter.vpy" in written_data[-1]

        # String literals in code should NOT be corrupted
        msg3 = '{"params":{"content":"val = \\"file:///workspace_custom\\""}}'
        server._on_ws_message(msg3)
        assert b"file:///workspace_custom" in written_data[-1]

    def test_process_finished_closes_clients(self, mocker: MockerFixture) -> None:
        config = LSPConfig(
            id="test_lsp",
            name="Test Server",
            command=["test-lsp", "--stdio"],
            language="python",
            file_events_pattern="**/*.py",
        )
        server = LSPProcessServer(config)
        mock_client = mocker.MagicMock()
        mock_client.isValid.return_value = True
        server.clients.add(mock_client)

        server._on_process_finished(1, QProcess.ExitStatus.CrashExit)
        assert mock_client.close.call_count == 1
        assert len(server.clients) == 0


class TestEditorShortcuts:
    def test_shortcuts_uniqueness(self) -> None:
        shortcut_ids = [str(s.definition) for s in EditorShortcut]
        assert len(shortcut_ids) == len(set(shortcut_ids))
        assert len(shortcut_ids) == 12

        for shortcut in EditorShortcut:
            assert shortcut.definition.startswith("jet_vsview_editor.")
            assert shortcut.definition.label

    def test_restart_lsp_shortcut_present(self) -> None:
        assert EditorShortcut.RESTART_LSP in EditorShortcut
        assert str(EditorShortcut.RESTART_LSP.definition) == "jet_vsview_editor.restart_lsp"
        assert EditorShortcut.RESTART_LSP.definition.label == "Restart Language Server"

    def test_monaco_bridge_restart_lsp_signal(self) -> None:
        bridge = MonacoBridge()
        received: list[tuple[JsToPyCommand, dict[str, Any]]] = []
        bridge.commandReceived.connect(lambda cmd, payload: received.append((cmd, payload)))
        bridge.dispatchFromJs(JsToPyCommand.RESTART_LSP_REQUESTED, "{}")
        assert received == [(JsToPyCommand.RESTART_LSP_REQUESTED, {})]

    def test_monaco_bridge_session_state_signal(self) -> None:
        bridge = MonacoBridge()
        received: list[tuple[JsToPyCommand, dict[str, Any]]] = []
        session: list[TabInfo] = [
            {
                "uri": "file:///workspace/script.py",
                "title": "script.py",
                "isMain": True,
                "isDirty": True,
                "language": "python",
                "content": "clip = core.std.BlankClip()\n",
            }
        ]
        bridge.commandReceived.connect(lambda cmd, payload: received.append((cmd, payload)))

        bridge.dispatchFromJs(JsToPyCommand.SESSION_STATE_CHANGED, json.dumps({"tabs": session}))

        assert received == [(JsToPyCommand.SESSION_STATE_CHANGED, {"tabs": session})]

    def test_monaco_bridge_content_state_tracking(self) -> None:
        bridge = MonacoBridge()
        assert bridge.content == ""
        assert bridge.main_content == ""

        bridge.dispatchFromJs(JsToPyCommand.CONTENT_CHANGED, json.dumps({"content": "active = 1"}))
        bridge.dispatchFromJs(JsToPyCommand.MAIN_CONTENT_CHANGED, json.dumps({"content": "main = 2"}))

        assert bridge.content == "active = 1"
        assert bridge.main_content == "main = 2"

    def test_workspace_shortcuts_contain_all_definitions(self) -> None:
        workspace_defs = {str(s) for s in EditorWorkspace.shortcuts}
        enum_defs = {str(s.definition) for s in EditorShortcut}
        assert workspace_defs == enum_defs


class TestGlobalConsoleHub:
    def test_register_and_unregister_bridge(self, mocker: MockerFixture) -> None:
        bridge = MonacoBridge()
        spy_dispatch = mocker.spy(bridge, "dispatch")

        GlobalConsoleHub.register(bridge)
        GlobalConsoleHub.set_width(bridge, 120)
        assert GlobalConsoleHub.target_width == 120

        payload: ConsoleAppendPayload = {"stream": "stdout", "text": "test output\n"}
        GlobalConsoleHub.dispatch(payload)
        spy_dispatch.assert_called_once_with("console.append", payload)

        spy_dispatch.reset_mock()
        GlobalConsoleHub.unregister(bridge)

        GlobalConsoleHub.dispatch({"stream": "stdout", "text": "after unregister\n"})
        spy_dispatch.assert_not_called()

    def test_console_stream_redirector_recursion_depth(self, mocker: MockerFixture) -> None:
        redirector = ConsoleStreamRedirector("stdout")
        mock_dispatch = mocker.patch.object(GlobalConsoleHub, "dispatch")

        try:
            # When depth == 0, dispatch is triggered
            setattr(IN_LOGGING, "depth", 0)
            redirector.write("test normal output\n")
            mock_dispatch.assert_called_once()

            # When depth > 0 (inside logging emit), dispatch is suppressed
            mock_dispatch.reset_mock()
            setattr(IN_LOGGING, "depth", 1)
            redirector.write("log formatted output\n")
            mock_dispatch.assert_not_called()
        finally:
            setattr(IN_LOGGING, "depth", 0)

    def test_thread_safe_dispatch(self) -> None:
        bridge = MonacoBridge()
        GlobalConsoleHub.register(bridge)

        def worker() -> None:
            for _ in range(50):
                GlobalConsoleHub.dispatch({"stream": "stdout", "text": "chunk\n"})

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        GlobalConsoleHub.unregister(bridge)

    def test_console_stream_redirector_none_stream(self, mocker: MockerFixture) -> None:
        redirector = ConsoleStreamRedirector("stdout")
        redirector._original_stream = None
        mock_dispatch = mocker.patch.object(GlobalConsoleHub, "dispatch")

        assert redirector.write("test none stream") == len("test none stream")
        mock_dispatch.assert_called_once()
        assert redirector.writable() is True
        assert redirector.isatty() is False
        redirector.flush()  # should not throw


def _make_drag_enter_event(mime_data: QMimeData) -> QDragEnterEvent:
    return QDragEnterEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _make_drag_move_event(mime_data: QMimeData) -> QDragMoveEvent:
    return QDragMoveEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _make_drop_event(mime_data: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _set_editor_tabs(dock: MonacoEditorDock, tabs: list[TabInfo], active_uri: str | None = None) -> None:
    dock.editor.bridge.commandReceived.emit(JsToPyCommand.TAB_STATE_CHANGED, {"tabs": tabs})
    if active_uri is not None:
        dock.editor.bridge.commandReceived.emit(JsToPyCommand.ACTIVE_TAB_CHANGED, {"uri": active_uri})
    for tab in tabs:
        if tab.get("isMain"):
            dock.editor.bridge.commandReceived.emit(JsToPyCommand.MAIN_TAB_CHANGED, {"uri": tab["uri"]})
            break


class TestEditorDragAndDrop:
    @pytest.fixture
    def editor_widget(self, qtbot: QtBot) -> Generator[MonacoEditorWidget, None, None]:
        parent = QWidget()
        qtbot.addWidget(parent)
        widget = MonacoEditorWidget(parent)
        try:
            yield widget
        finally:
            page = widget.page()
            page.deleteLater()
            widget.deleteLater()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @pytest.fixture
    def editor_dock(self, qtbot: QtBot, mocker: MockerFixture) -> Generator[MonacoEditorDock, None, None]:
        mocker.patch.object(MonacoEditorWidget, "load")
        parent = QWidget()
        qtbot.addWidget(parent)
        settings = mocker.MagicMock()
        settings.global_.options.theme = "dark"
        dock = MonacoEditorDock(parent, mocker.MagicMock(), mocker.MagicMock(), settings)
        try:
            yield dock
        finally:
            page = dock.editor.page()
            page.deleteLater()
            dock.deleteLater()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_extract_supported_script_files_filtering(self, tmp_path: Path) -> None:
        py_file = tmp_path / "script.py"
        py_file.write_text("print(1)", encoding="utf-8")
        vpy_file = tmp_path / "script.VPY"
        vpy_file.write_text("print(2)", encoding="utf-8")
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("text", encoding="utf-8")
        sub_dir = tmp_path / "folder"
        sub_dir.mkdir()
        non_existent = tmp_path / "missing.py"

        mime = QMimeData()
        mime.setUrls(
            [
                QUrl.fromLocalFile(str(py_file)),
                QUrl.fromLocalFile(str(vpy_file)),
                QUrl.fromLocalFile(str(txt_file)),
                QUrl.fromLocalFile(str(sub_dir)),
                QUrl.fromLocalFile(str(non_existent)),
            ]
        )

        extracted = extract_supported_script_files(mime)
        assert extracted == [py_file, vpy_file]

    def test_editor_widget_drag_events_accept_valid_script(
        self, editor_widget: MonacoEditorWidget, tmp_path: Path
    ) -> None:
        script = tmp_path / "filter.vpy"
        script.touch()

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(script))])

        enter_event = _make_drag_enter_event(mime)
        editor_widget.dragEnterEvent(enter_event)
        assert enter_event.isAccepted() is True

        move_event = _make_drag_move_event(mime)
        editor_widget.dragMoveEvent(move_event)
        assert move_event.isAccepted() is True

    def test_editor_widget_drag_events_reject_invalid_files(
        self, editor_widget: MonacoEditorWidget, tmp_path: Path
    ) -> None:
        doc = tmp_path / "readme.md"
        doc.touch()

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(doc))])

        enter_event = _make_drag_enter_event(mime)
        editor_widget.dragEnterEvent(enter_event)
        assert enter_event.isAccepted() is False

        move_event = _make_drag_move_event(mime)
        editor_widget.dragMoveEvent(move_event)
        assert move_event.isAccepted() is False

    def test_editor_widget_drop_event_emits_signal(self, editor_widget: MonacoEditorWidget, tmp_path: Path) -> None:
        script1 = tmp_path / "a.py"
        script1.touch()
        script2 = tmp_path / "b.vpy"
        script2.touch()

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(script1)), QUrl.fromLocalFile(str(script2))])

        received: list[list[Path]] = []
        editor_widget.filesDropped.connect(received.append)

        drop_event = _make_drop_event(mime)
        editor_widget.dropEvent(drop_event)

        assert drop_event.isAccepted() is True
        assert received == [[script1, script2]]

    def test_editor_widget_drop_event_rejects_unsupported(
        self, editor_widget: MonacoEditorWidget, tmp_path: Path
    ) -> None:
        doc = tmp_path / "data.json"
        doc.touch()

        emitted: list[list[Path]] = []
        editor_widget.filesDropped.connect(emitted.append)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(doc))])

        drop_event = _make_drop_event(mime)
        editor_widget.dropEvent(drop_event)

        assert drop_event.isAccepted() is False
        assert len(emitted) == 0

    def test_monaco_dock_open_file_success(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        script_file = tmp_path / "script.py"
        script_file.write_text("import vapoursynth as vs\n", encoding="utf-8")

        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(script_file) is True
        assert editor_dock.active_filepath == script_file.resolve()
        mock_dispatch.assert_called_once_with(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": script_file.resolve().as_uri(),
                "content": "import vapoursynth as vs\n",
                "language": "python",
                "isMain": False,
            },
        )

    def test_monaco_dock_open_file_replaces_default_script_tab(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri="file:///workspace/script.py",
                    title="script.py",
                    isMain=True,
                    isDirty=False,
                    language="python",
                    content=editor_dock.default_content,
                )
            ],
            active_uri="file:///workspace/script.py",
        )

        script_file = tmp_path / "custom.py"
        script_file.write_text("clip = core.std.BlankClip()\n", encoding="utf-8")

        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(script_file) is True
        assert editor_dock.active_filepath == script_file.resolve()
        mock_dispatch.assert_called_once_with(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": script_file.resolve().as_uri(),
                "content": "clip = core.std.BlankClip()\n",
                "language": "python",
                "isMain": True,
                "replaceUri": "file:///workspace/script.py",
            },
        )
        assert len(editor_dock.open_tabs) == 1
        assert editor_dock.open_tabs[0]["uri"] == script_file.resolve().as_uri()
        assert editor_dock.open_tabs[0]["isMain"] is True

    def test_monaco_dock_open_file_does_not_replace_dirty_default_tab(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri="file:///workspace/script.py",
                    title="script.py",
                    isMain=True,
                    isDirty=True,
                    language="python",
                    content="edited content",
                )
            ],
            active_uri="file:///workspace/script.py",
        )

        script_file = tmp_path / "custom.py"
        script_file.write_text("new content", encoding="utf-8")

        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(script_file) is True
        mock_dispatch.assert_called_once_with(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": script_file.resolve().as_uri(),
                "content": "new content",
                "language": "python",
                "isMain": False,
            },
        )
        assert len(editor_dock.open_tabs) == 2
        assert editor_dock.open_tabs[0]["uri"] == "file:///workspace/script.py"
        assert editor_dock.open_tabs[1]["uri"] == script_file.resolve().as_uri()

    def test_monaco_dock_open_files_batch_replaces_default_only_once(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri="file:///workspace/script.py",
                    title="script.py",
                    isMain=True,
                    isDirty=False,
                    language="python",
                    content=editor_dock.default_content,
                )
            ],
            active_uri="file:///workspace/script.py",
        )

        f1 = tmp_path / "first.py"
        f1.write_text("first", encoding="utf-8")
        f2 = tmp_path / "second.py"
        f2.write_text("second", encoding="utf-8")

        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        editor_dock.open_files([f1, f2])

        assert mock_dispatch.call_count == 2
        mock_dispatch.assert_any_call(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": f1.resolve().as_uri(),
                "content": "first",
                "language": "python",
                "isMain": True,
                "replaceUri": "file:///workspace/script.py",
            },
        )
        mock_dispatch.assert_any_call(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": f2.resolve().as_uri(),
                "content": "second",
                "language": "python",
                "isMain": False,
            },
        )
        open_uris = [t["uri"] for t in editor_dock.open_tabs]
        assert open_uris == [f1.resolve().as_uri(), f2.resolve().as_uri()]

    def test_monaco_dock_open_file_replaces_active_default_tab_when_multiple_tabs_open(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        other_file = tmp_path / "other.py"
        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri=other_file.as_uri(),
                    title="other.py",
                    isMain=False,
                    isDirty=False,
                    language="python",
                    content="other",
                ),
                TabInfo(
                    uri=MonacoEditorDock.DEFAULT_SCRIPT_URI,
                    title="script.py",
                    isMain=True,
                    isDirty=False,
                    language="python",
                    content=editor_dock.default_content,
                ),
            ],
            active_uri=MonacoEditorDock.DEFAULT_SCRIPT_URI,
        )

        custom_file = tmp_path / "custom.py"
        custom_file.write_text("custom", encoding="utf-8")

        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(custom_file) is True
        mock_dispatch.assert_called_once_with(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": custom_file.resolve().as_uri(),
                "content": "custom",
                "language": "python",
                "isMain": True,
                "replaceUri": MonacoEditorDock.DEFAULT_SCRIPT_URI,
            },
        )
        uris = [t["uri"] for t in editor_dock.open_tabs]
        assert uris == [other_file.as_uri(), custom_file.resolve().as_uri()]

    def test_monaco_dock_open_file_preserves_unsaved_content_in_tab_cache(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        script_file = tmp_path / "existing.py"
        script_file.write_text("disk content", encoding="utf-8")

        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri=script_file.resolve().as_uri(),
                    title="existing.py",
                    isMain=False,
                    isDirty=True,
                    language="python",
                    content="unsaved editor edits",
                )
            ],
        )
        mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(script_file) is True
        assert editor_dock.open_tabs[0].get("content") == "unsaved editor edits"

    def test_monaco_dock_open_file_normalizes_uri_lookup(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        script_file = tmp_path / "script.py"
        script_file.write_text("code", encoding="utf-8")

        # Simulate Monaco URI on Windows with percent-encoded drive colon or lower-case drive
        resolved = script_file.resolve()
        if resolved.drive:
            drive_colon = resolved.drive
            monaco_uri = resolved.as_uri().replace(f"///{drive_colon}", f"///{drive_colon[0].lower()}%3A")
        else:
            monaco_uri = resolved.as_uri()

        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri=monaco_uri,
                    title="script.py",
                    isMain=False,
                    isDirty=False,
                    language="python",
                    content="code",
                )
            ],
        )
        mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        assert editor_dock.open_file(script_file) is True
        # Must not add duplicate TabInfo
        assert len(editor_dock.open_tabs) == 1

    def test_monaco_dock_on_editor_ready_replaces_default_tab_on_replay(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        script_file = tmp_path / "replayed.py"
        _set_editor_tabs(
            editor_dock,
            [
                TabInfo(
                    uri=script_file.as_uri(),
                    title="replayed.py",
                    isMain=True,
                    isDirty=False,
                    language="python",
                    content="print('replayed')",
                )
            ],
        )
        mocker.patch.object(editor_dock, "_generate_stubs")
        mock_dispatch = mocker.patch.object(editor_dock.editor.bridge, "dispatch")
        editor_dock._on_editor_ready()
        mock_dispatch.assert_any_call(
            PyToJsCommand.OPEN_TAB,
            {
                "uri": script_file.as_uri(),
                "content": "print('replayed')",
                "language": "python",
                "isMain": True,
                "replaceUri": MonacoEditorDock.DEFAULT_SCRIPT_URI,
            },
        )

    def test_monaco_dock_open_file_failure(self, editor_dock: MonacoEditorDock, tmp_path: Path) -> None:
        non_existent = tmp_path / "does_not_exist.py"
        assert editor_dock.open_file(non_existent) is False

    def test_monaco_dock_open_files(self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture) -> None:
        spy_open = mocker.spy(editor_dock, "open_file")
        f1 = tmp_path / "one.py"
        f1.touch()
        f2 = tmp_path / "two.vpy"
        f2.touch()

        editor_dock.open_files([f1, f2])
        assert spy_open.call_count == 2

    def test_monaco_dock_drag_and_drop_events(
        self, editor_dock: MonacoEditorDock, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        spy_open_files = mocker.spy(editor_dock, "open_files")
        script = tmp_path / "dock_test.py"
        script.touch()

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(script))])

        enter_event = _make_drag_enter_event(mime)
        editor_dock.dragEnterEvent(enter_event)
        assert enter_event.isAccepted() is True

        move_event = _make_drag_move_event(mime)
        editor_dock.dragMoveEvent(move_event)
        assert move_event.isAccepted() is True

        drop_event = _make_drop_event(mime)
        editor_dock.dropEvent(drop_event)
        assert drop_event.isAccepted() is True
        spy_open_files.assert_called_once_with([script])

    def test_stack_dock_rejects_drops(self, qtbot: QtBot) -> None:
        parent = QWidget()
        qtbot.addWidget(parent)
        stack = QWidget(parent)
        stack_dock = StackDock(parent, stack)

        assert stack_dock.acceptDrops() is False
