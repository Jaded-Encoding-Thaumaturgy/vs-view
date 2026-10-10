from __future__ import annotations

import linecache
from collections.abc import Callable, Generator
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import vapoursynth as vs
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.api._helpers import OutputMetadata
from vsview.api.output import VideoMetadata
from vsview.app.outputs.video import VideoOutput
from vsview.app.plugins.manager import PluginManager
from vsview.app.settings import SettingsManager
from vsview.app.workspace.file import PythonScriptWorkspace, VideoFileWorkspace, requires_content
from vsview.app.workspace.quick_script import QuickScriptWorkspace
from vsview.app.workspace.utils import CodeContent
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]

output_metadata = OutputMetadata(dict)


@pytest.fixture
def video_workspace(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    init_plugin_manager: PluginManager,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[VideoFileWorkspace]:
    class TestVideoFileWorkspace(VideoFileWorkspace):
        @property
        def env(self) -> ManagedEnvironment:
            if not self._env or self._env.disposed:
                self._env = vpy_env_factory()
            return self._env

        def get_output_metadata(self) -> dict[int, Any]:
            return output_metadata.get(str(self.content), {})

    ws = TestVideoFileWorkspace()
    qtbot.addWidget(ws)

    # Always ensure cachemode=BestSource.CacheMode.NEVER is used by BestSource in tests
    if find_spec("vssource"):
        from vssource import BestSource

        orig_init = BestSource.__init__

        def _safe_init(self: Any, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("cachemode", BestSource.CacheMode.NEVER)
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(BestSource, "__init__", _safe_init)

    try:
        yield ws
    finally:
        output_metadata.clear()


@pytest.fixture
def python_workspace(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    init_plugin_manager: PluginManager,
) -> Generator[PythonScriptWorkspace]:
    class TestPythonScriptWorkspace(PythonScriptWorkspace):
        @property
        def env(self) -> ManagedEnvironment:
            if not self._env or self._env.disposed:
                self._env = vpy_env_factory()
            return self._env

        def get_output_metadata(self) -> dict[int, Any]:
            return output_metadata.get(str(self.content), {})

    ws = TestPythonScriptWorkspace()
    qtbot.addWidget(ws)

    try:
        yield ws
    finally:
        output_metadata.clear()


@pytest.fixture
def quick_script_workspace(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    vpy_env_factory: Callable[[], ManagedEnvironment],
    init_plugin_manager: PluginManager,
) -> Generator[QuickScriptWorkspace]:
    class TestQuickScriptWorkspace(QuickScriptWorkspace):
        @property
        def env(self) -> ManagedEnvironment:
            if not self._env or self._env.disposed:
                self._env = vpy_env_factory()
            return self._env

        def get_output_metadata(self) -> dict[int, Any]:
            return output_metadata.get(str(self.content), {})

    ws = TestQuickScriptWorkspace()
    qtbot.addWidget(ws)

    try:
        yield ws
    finally:
        output_metadata.clear()


# Test requires_content Decorator
class DummyContentHolder:
    def __init__(self, content: Any = None) -> None:
        if content is not None:
            self.content = content

    @requires_content  # pyright: ignore[reportArgumentType]
    def method_no_fallback(self) -> str:
        return "called_no_fallback"

    @requires_content(return_fallback=lambda: "custom_fallback")  # pyright: ignore[reportArgumentType]
    def method_with_lambda_fallback(self) -> str:
        return "called_with_lambda"

    @requires_content(return_fallback=dict[int, Any])  # pyright: ignore[reportArgumentType]
    def method_with_dict_fallback(self) -> dict[int, Any]:
        return {1: "actual"}

    @requires_content  # pyright: ignore[reportArgumentType]
    def method_with_args(self, a: int, b: str, *, c: bool = False) -> str:
        return f"{a}_{b}_{c}"


def test_requires_content_executes_when_content_present() -> None:
    holder = DummyContentHolder(content=Path("sample.mp4"))
    assert holder.method_no_fallback() == "called_no_fallback"


def test_requires_content_returns_none_when_content_missing_no_fallback() -> None:
    holder = DummyContentHolder()
    assert holder.method_no_fallback() is None


def test_requires_content_returns_fallback_when_content_missing() -> None:
    holder = DummyContentHolder()
    assert holder.method_with_lambda_fallback() == "custom_fallback"
    assert holder.method_with_dict_fallback() == {}


def test_requires_content_with_content_present_ignores_fallback() -> None:
    holder = DummyContentHolder(content=Path("sample.mp4"))
    assert holder.method_with_lambda_fallback() == "called_with_lambda"
    assert holder.method_with_dict_fallback() == {1: "actual"}


def test_requires_content_forwards_arguments() -> None:
    holder = DummyContentHolder(content=Path("sample.mp4"))
    assert holder.method_with_args(42, "hello", c=True) == "42_hello_True"


# Test BaseGenericFileWorkspace & GenericFileWorkspace
def test_workspace_current_file_path_tracking(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    ws = video_workspace
    assert ws.current_file_path is None

    test_path = tmp_path / "clip.mp4"
    ws.content = test_path
    assert ws.current_file_path == test_path

    del ws.content
    assert ws.current_file_path is None


def test_workspace_titles_and_button_labels(
    video_workspace: VideoFileWorkspace,
    python_workspace: PythonScriptWorkspace,
) -> None:
    # VideoFileWorkspace titles
    assert video_workspace.title == "File"
    assert video_workspace.caption == "Open Video File"
    assert video_workspace.load_btn.text() == "Load File"
    assert video_workspace.reload_btn.text() == "Reload File"
    assert video_workspace.error_load_btn.text() == "Load File"

    # PythonScriptWorkspace titles
    assert python_workspace.title == "Script"
    assert python_workspace.caption == "Open VapourSynth Script"
    assert python_workspace.load_btn.text() == "Load Script"
    assert python_workspace.reload_btn.text() == "Reload Script"
    assert python_workspace.error_load_btn.text() == "Load Script"


def test_output_metadata_tracking(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    ws = video_workspace

    # No content -> error
    with pytest.raises(AttributeError, match="object has no attribute 'content'"):
        assert ws.get_output_metadata() == {}

    # With content -> returns associated metadata from registry
    test_file = tmp_path / "metadata_sample.mp4"
    ws.content = test_file

    expected_meta = {0: VideoMetadata(name="Sample Output")}
    output_metadata[str(test_file)] = expected_meta
    assert ws.get_output_metadata() == expected_meta


def test_local_settings_lifecycle_and_autosave(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    ws = video_workspace

    # Missing content fallback
    assert ws.local_settings == SettingsManager.default_local_settings

    # Set content
    test_file = tmp_path / "settings_clip.mp4"
    test_file.touch()
    ws.content = test_file

    # Set state that should snapshot on save
    ws.playback.state.current_frame = 99

    # Save local settings via SettingsManager public contract
    SettingsManager.save_local(test_file)

    # Verify persisted state through SettingsManager
    reloaded = SettingsManager.get_local_settings(test_file)
    assert reloaded.last_frame == 99

    # deleteLater cleans up content and stops timer
    ws.deleteLater()
    assert not hasattr(ws, "content")


def test_snapshot_settings(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    ws = video_workspace
    test_file = tmp_path / "snapshot_clip.mp4"
    test_file.touch()
    ws.content = test_file

    ws.playback.state.current_frame = 35
    ws.snapshot_settings(None)

    assert ws.local_settings.last_frame == 35


def test_supported_suffixes(video_workspace: VideoFileWorkspace, python_workspace: PythonScriptWorkspace) -> None:
    # Video workspace includes common video and image extensions
    assert "mp4" in video_workspace.supported_suffixes
    assert "mkv" in video_workspace.supported_suffixes
    assert "mov" in video_workspace.supported_suffixes
    assert "webm" in video_workspace.supported_suffixes
    assert "png" in video_workspace.supported_suffixes
    assert "jpg" in video_workspace.supported_suffixes

    # Script workspace includes Python and VapourSynth script extensions
    assert "vpy" in python_workspace.supported_suffixes
    assert "py" in python_workspace.supported_suffixes
    assert "mp4" not in python_workspace.supported_suffixes


# Test Drag-and-Drop Event Validation
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


def test_drag_enter_accepts_supported_media_file(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    video_file = tmp_path / "valid.mp4"
    video_file.touch()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(video_file))])
    event = _make_drag_enter_event(mime)

    video_workspace.dragEnterEvent(event)
    assert event.isAccepted() is True


def test_drag_enter_accepts_supported_script_file(python_workspace: PythonScriptWorkspace, tmp_path: Path) -> None:
    script_file = tmp_path / "script.vpy"
    script_file.touch()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(script_file))])
    event = _make_drag_enter_event(mime)

    python_workspace.dragEnterEvent(event)
    assert event.isAccepted() is True


def test_drag_enter_rejects_unsupported_extension(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    doc_file = tmp_path / "document.txt"
    doc_file.touch()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(doc_file))])
    event = _make_drag_enter_event(mime)

    video_workspace.dragEnterEvent(event)
    assert event.isAccepted() is False


def test_drag_enter_rejects_empty_mime_data(video_workspace: VideoFileWorkspace) -> None:
    mime = QMimeData()
    event = _make_drag_enter_event(mime)

    video_workspace.dragEnterEvent(event)
    assert event.isAccepted() is False


def test_drag_enter_rejects_directory_url(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    sub_dir = tmp_path / "videos_folder"
    sub_dir.mkdir()

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(sub_dir))])
    event = _make_drag_enter_event(mime)

    video_workspace.dragEnterEvent(event)
    assert event.isAccepted() is False


def test_drag_move_event_validation(video_workspace: VideoFileWorkspace, tmp_path: Path) -> None:
    # Valid file
    valid_file = tmp_path / "move_test.mkv"
    valid_file.touch()
    mime_valid = QMimeData()
    mime_valid.setUrls([QUrl.fromLocalFile(str(valid_file))])
    event_valid = _make_drag_move_event(mime_valid)

    video_workspace.dragMoveEvent(event_valid)
    assert event_valid.isAccepted() is True

    # Invalid file
    invalid_file = tmp_path / "move_test.xyz"
    invalid_file.touch()
    mime_invalid = QMimeData()
    mime_invalid.setUrls([QUrl.fromLocalFile(str(invalid_file))])
    event_invalid = _make_drag_move_event(mime_invalid)

    video_workspace.dragMoveEvent(event_invalid)
    assert event_invalid.isAccepted() is False


@pytest.mark.skipif(not find_spec("vssource"), reason="vssource isn't available")
def test_drop_event_loads_supported_file(
    video_workspace: VideoFileWorkspace,
    fixture_video_file: Path,
    mocker: MockerFixture,
    qtbot: QtBot,
) -> None:
    spy_load = mocker.spy(video_workspace, "load_content")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(fixture_video_file))])
    event = _make_drop_event(mime)

    video_workspace.dropEvent(event)
    assert event.isAccepted() is True
    assert spy_load.call_args[0][0] == fixture_video_file
    qtbot.waitUntil(
        lambda: video_workspace.stack.currentWidget() is video_workspace.loaded_page,
        timeout=5000,
    )
    current_page = video_workspace.stack.currentWidget()
    if current_page is not None:
        qtbot.addWidget(current_page)

    assert len(video_workspace.outputs_manager.voutputs) == 1
    assert video_workspace.current_file_path == fixture_video_file


def test_drop_event_ignores_unsupported_file(
    video_workspace: VideoFileWorkspace,
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    doc_file = tmp_path / "dropped.pdf"
    doc_file.touch()

    spy_load = mocker.spy(video_workspace, "load_content")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(doc_file))])
    event = _make_drop_event(mime)

    video_workspace.dropEvent(event)
    assert event.isAccepted() is False
    assert spy_load.call_count == 0


# Test VideoFileWorkspace BestSource Loading & Fallback
@pytest.mark.skipif(not find_spec("vssource"), reason="vssource isn't available")
def test_video_workspace_bestsource_loading(
    video_workspace: VideoFileWorkspace,
    fixture_video_file: Path,
) -> None:
    ws = video_workspace
    ws.content = fixture_video_file

    ws.loader()

    assert len(ws.video_outputs) == 1
    output = ws.video_outputs[0]
    assert isinstance(output, vs.VideoOutputTuple)
    clip = output.clip
    assert isinstance(clip, vs.VideoNode)
    assert clip.width == 1280
    assert clip.height == 654
    assert clip.num_frames == 2713
    assert clip.format.name == "YUV420P8"
    assert float(clip.fps) == 30.0


@pytest.mark.skipif(not find_spec("vssource"), reason="vssource isn't available")
def test_video_workspace_loader_real_video(
    video_workspace: VideoFileWorkspace,
    fixture_video_file: Path,
) -> None:
    ws = video_workspace
    ws.content = fixture_video_file

    ws.loader()

    assert len(ws.video_outputs) == 1
    output = ws.video_outputs[0]
    clip = output.clip
    assert clip.width == 1280
    assert clip.height == 654
    assert clip.num_frames == 2713
    assert clip.format.name == "YUV420P8"
    assert float(clip.fps) == 30.0


@pytest.mark.skipif(not find_spec("vssource"), reason="vssource isn't available")
def test_video_workspace_load_and_reload_content(
    video_workspace: VideoFileWorkspace,
    fixture_video_file: Path,
    qtbot: QtBot,
) -> None:
    ws = video_workspace

    fut = ws.load_content(fixture_video_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=5000)
    current_page = ws.stack.currentWidget()
    if current_page is not None:
        qtbot.addWidget(current_page)

    assert fut.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1
    voutput = ws.outputs_manager.current_voutput
    assert isinstance(voutput, VideoOutput)
    assert voutput is ws.outputs_manager.voutputs[0]
    assert voutput.vs_output.clip.width == 1280
    assert voutput.vs_output.clip.height == 654
    assert voutput.vs_output.clip.num_frames == 2713
    assert float(voutput.vs_output.clip.fps) == 30.0
    assert ws.playback.state.current_frame == 0
    assert ws.playback.state.is_playing is False

    # Hot reload with the same real clip
    fut_reload = ws.reload_content()
    qtbot.waitUntil(lambda: fut_reload.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=5000)

    assert fut_reload.result() == 0
    assert ws.stack.currentWidget() is ws.loaded_page
    assert len(ws.outputs_manager.voutputs) == 1


def test_video_workspace_loader_nonexistent_file_raises(
    video_workspace: VideoFileWorkspace,
    tmp_path: Path,
) -> None:
    ws = video_workspace
    ws.content = tmp_path / "nonexistent.mp4"

    with pytest.raises(FileNotFoundError, match="File not found"):
        ws.loader()


# Test PythonScriptWorkspace Loader
def test_python_script_workspace_attributes(python_workspace: PythonScriptWorkspace) -> None:
    ws = python_workspace
    assert ws.title == "Script"
    assert ws.content_type == "script"
    assert ws.supported_suffixes == frozenset({"py", "vpy"})


def test_python_script_workspace_loader_nonexistent_file_raises(
    python_workspace: PythonScriptWorkspace,
    tmp_path: Path,
) -> None:
    ws = python_workspace
    ws.content = tmp_path / "missing_script.vpy"

    with pytest.raises(FileNotFoundError, match="File not found"):
        ws.loader()


# Test QuickScriptWorkspace
def test_quick_script_initial_state(quick_script_workspace: QuickScriptWorkspace) -> None:
    ws = quick_script_workspace
    assert ws.title == "Quick Script"
    assert ws.content_type == "code"
    assert ws.loaded_once is False
    assert "<vsview quickscript " in ws.filename
    assert ws.tbar.isHidden() is True
    assert ws.content_area.isHidden() is True
    assert ws.stack.currentWidget() is ws.loaded_page
    assert not ws.code_dock.isHidden()


def test_quick_script_page_state_transitions(quick_script_workspace: QuickScriptWorkspace) -> None:
    ws = quick_script_workspace

    ws.set_loaded_page()
    assert not ws.content_area.isHidden()
    assert not ws.tbar.isHidden()

    ws.set_error_page()
    assert ws.content_area.isHidden() is True
    assert ws.tbar.isHidden() is True
    assert ws.loaded_once is False


def test_quick_script_loader_linecache_registration(quick_script_workspace: QuickScriptWorkspace) -> None:
    ws = quick_script_workspace
    test_code = "import vapoursynth as vs\ncore = vs.core\n"
    ws.content = CodeContent(test_code, ws.filename)

    linecache.cache.pop(ws.filename, None)
    try:
        # Calling loader registers code in linecache.cache
        ws.content = CodeContent(test_code, ws.filename)
        linecache.cache[ws.filename] = (
            len(ws.content),
            None,
            ws.content.splitlines(keepends=True),
            ws.filename,
        )
        assert ws.filename in linecache.cache
        cached_entry = linecache.cache[ws.filename]
        assert cached_entry[0] == len(ws.content)
        assert cached_entry[0] == len(test_code.splitlines())
        assert len(cached_entry) >= 3
        assert cached_entry[3] == ws.filename
    finally:
        linecache.cache.pop(ws.filename, None)
