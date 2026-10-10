from __future__ import annotations

import sys
from pathlib import Path

import pytest
from jetpytools import CustomValueError
from PySide6.QtCore import QObject
from pytest_mock import MockerFixture
from vsview_editor.console import IN_LOGGING, ConsoleStreamRedirector, GlobalConsoleHub
from vsview_editor.lsp import LSPConfig, LSPProcessManager, LSPProcessServer
from vsview_editor.utils import ContentPath, SafeSlot, WorkspaceUri
from vsview_editor.web import MonacoBridge
from vsview_editor.workspace import EditorShortcut

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


class TestSafeSlot:
    def test_safe_slot_success(self) -> None:
        @SafeSlot(int, result=int, fallback=-1)
        def multiply(x: int) -> int:
            return x * 2

        assert multiply(5) == 10

    def test_safe_slot_catches_exception_and_returns_fallback(self) -> None:
        @SafeSlot(result=str, fallback="error_fallback")
        def failing() -> str:
            raise RuntimeError("Something broke")

        assert failing() == "error_fallback"

    def test_safe_slot_default_fallback_is_none(self) -> None:
        @SafeSlot()
        def failing_default() -> str:
            raise ValueError("Invalid")

        assert failing_default() is None


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
        called = False

        def _on_restart() -> None:
            nonlocal called
            called = True

        bridge.restartLspRequested.connect(_on_restart)
        bridge.requestRestartLsp()
        assert called


class TestGlobalConsoleHub:
    def test_register_and_unregister_bridge(self, mocker: MockerFixture) -> None:
        bridge = MonacoBridge()
        spy_dispatch = mocker.spy(bridge, "dispatch")

        GlobalConsoleHub.register(bridge)
        GlobalConsoleHub.set_width(bridge, 120)
        assert GlobalConsoleHub.target_width == 120

        payload = {"stream": "stdout", "text": "test output\n"}
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
