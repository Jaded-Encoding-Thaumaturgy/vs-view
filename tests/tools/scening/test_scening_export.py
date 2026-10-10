from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import vapoursynth as vs
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from vsengine.policy import ManagedEnvironment

from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins.contracts import VideoOutputProxy
from vsview.app.tools.scening.models import RangeFrame
from vsview.app.tools.scening.plugin import GlobalSettings
from vsview.app.tools.scening.worker import FFV1_ARGS, H264_ARGS, ExportJob, ExportQueueManager

if TYPE_CHECKING:
    from tests.harness import PluginTestHarness

core = vs.core
pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("initial-core")]


def make_video_output(
    clip: vs.VideoNode | None = None,
    num_frames: int = 24,
    color_family: vs.ColorFamily = vs.RGB,
) -> VideoOutput:
    if clip is None:
        if color_family == vs.RGB:
            clip = core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=num_frames, fpsnum=24, fpsden=1)
            clip = core.std.SetFrameProps(
                clip,
                _Matrix=vs.MATRIX_RGB,
                _Primaries=vs.PRIMARIES_BT709,
                _Transfer=vs.TRANSFER_BT709,
            )
        else:
            clip = core.std.BlankClip(format=vs.YUV420P8, width=64, height=64, length=num_frames, fpsnum=24, fpsden=1)
            clip = core.std.SetFrameProps(
                clip,
                _Matrix=vs.MATRIX_BT709,
                _Primaries=vs.PRIMARIES_BT709,
                _Transfer=vs.TRANSFER_BT709,
            )
    packer = Packer()
    voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, packer)
    voutput.prepared_clip = packer.pack_clip(clip, None)
    return voutput


def make_voutput_proxy(vo: VideoOutput) -> VideoOutputProxy:
    return VideoOutputProxy(
        vo.vs_index,
        vo.vs_name,
        vo.vs_output,
        vo.props,
        vo.framedurs,
        vo.cum_durations,
        vo.midpoints,
        vo.kwargs,
        vo.info,
        vo.packer,
    )


@pytest.fixture
def sample_voutput_proxy() -> VideoOutputProxy:
    return make_voutput_proxy(make_video_output())


@pytest.fixture
def mock_settings() -> SimpleNamespace:
    return SimpleNamespace(global_=GlobalSettings(ffmpeg_path="ffmpeg", exclusive=False))


@pytest.fixture
def export_harness(
    plugin_harness: PluginTestHarness,
    vpy_env_factory: Callable[[], ManagedEnvironment],
) -> PluginTestHarness:
    plugin_harness.env = vpy_env_factory()
    return plugin_harness


@pytest.fixture
def manager(export_harness: PluginTestHarness, mock_settings: Any) -> ExportQueueManager:
    return ExportQueueManager(export_harness, mock_settings)  # pyright: ignore[reportArgumentType]


def test_export_queue_manager_lifecycle(
    qtbot: QtBot,
    export_harness: PluginTestHarness,
    manager: ExportQueueManager,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    assert export_harness.busy is False
    assert [t for t in threading.enumerate() if t.name == "ExportWorkerThread" and t.is_alive()] == []

    # Act 1: Shutdown when unused is an immediate no-op
    manager.shutdown(timeout=1.0)

    # Assert 1: Remains idle and unstarted
    assert export_harness.busy is False
    assert [t for t in threading.enumerate() if t.name == "ExportWorkerThread" and t.is_alive()] == []

    # Act 2: First enqueue triggers worker execution and public state change
    manager.enqueue([], sample_voutput_proxy, [Path("test.mp4")], [], "h264")

    # Assert 2: Observable signal emitted and worker thread is active
    qtbot.waitUntil(lambda: len(status_messages) == 1, timeout=2000)
    assert status_messages[0].startswith("Queued export of 1 clip(s). Queue size:")

    worker_threads_active = [t for t in threading.enumerate() if t.name == "ExportWorkerThread" and t.is_alive()]
    assert len(worker_threads_active) == 1
    assert isinstance(worker_threads_active[0], threading.Thread)
    assert worker_threads_active[0].name == "ExportWorkerThread"

    # Act 3: Shutdown terminates active worker
    manager.shutdown(timeout=1.0)

    # Assert 3: Observable state is idle and worker thread has exited
    qtbot.waitUntil(
        lambda: len([t for t in threading.enumerate() if t.name == "ExportWorkerThread" and t.is_alive()]) == 0,
        timeout=2000,
    )
    assert [t for t in threading.enumerate() if t.name == "ExportWorkerThread" and t.is_alive()] == []


def test_export_queue_manager_fifo_execution(
    qtbot: QtBot,
    mocker: MockerFixture,
    manager: ExportQueueManager,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    executed_jobs = list[int]()

    def mock_execute(job: ExportJob) -> None:
        idx = int(job.dest_files[0].stem.split("_")[-1])
        executed_jobs.append(idx)

    mocker.patch.object(manager, "_execute_job", side_effect=mock_execute)

    # Act
    for i in range(3):
        manager.enqueue(
            ranges=[RangeFrame(start=i, end=i + 1)],
            voutput=sample_voutput_proxy,
            dest_files=[Path(f"test_{i}.mp4")],
            ffmpeg_args=[],
            fmt="h264",
        )

    # Assert: wait for all 3 jobs to complete via observable list
    qtbot.waitUntil(lambda: executed_jobs == [0, 1, 2], timeout=3000)
    assert executed_jobs == [0, 1, 2]

    manager.shutdown(timeout=1.0)


def test_export_queue_manager_cancel_current(
    mocker: MockerFixture,
    manager: ExportQueueManager,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    job_started = threading.Event()
    cancelled_observed = threading.Event()

    def mock_execute(job: ExportJob) -> None:
        job_started.set()
        if job.cancel_event.wait(timeout=2.0):
            cancelled_observed.set()

    mocker.patch.object(manager, "_execute_job", side_effect=mock_execute)

    # Act
    manager.enqueue(
        ranges=[RangeFrame(start=0, end=1)],
        voutput=sample_voutput_proxy,
        dest_files=[Path("test_cancel.mp4")],
        ffmpeg_args=[],
        fmt="h264",
    )

    assert job_started.wait(timeout=2.0)
    manager.cancel_current(wait=True, timeout=2.0)

    # Assert
    assert cancelled_observed.is_set()
    manager.shutdown(timeout=1.0)


def test_export_queue_manager_cancel_all(
    mocker: MockerFixture,
    manager: ExportQueueManager,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    first_job_started = threading.Event()
    executed_jobs = list[str]()

    def mock_execute(job: ExportJob) -> None:
        executed_jobs.append(job.dest_files[0].stem)
        if job.dest_files[0].stem == "job_0":
            first_job_started.set()
            assert job.cancel_event.wait(timeout=2.0)

    mocker.patch.object(manager, "_execute_job", side_effect=mock_execute)

    # Act
    for i in range(4):
        manager.enqueue(
            ranges=[RangeFrame(start=i, end=i + 1)],
            voutput=sample_voutput_proxy,
            dest_files=[Path(f"job_{i}.mp4")],
            ffmpeg_args=[],
            fmt="h264",
        )

    assert first_job_started.wait(timeout=2.0)
    # Cancel all while job_0 is active; job_1, 2, 3 must be drained and skipped
    manager.cancel_all(wait=True, timeout=2.0)

    # Assert
    assert executed_jobs == ["job_0"]
    manager.shutdown(timeout=1.0)


def test_export_job_cleanup_on_cancel(
    tmp_path: Path,
    qtbot: QtBot,
    mocker: MockerFixture,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    dummy_file = tmp_path / "cancelled_output.mp4"
    dummy_file.write_bytes(b"dummy corrupted data")

    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    # Patch Popen so that when export starts, we simulate cancellation
    def mock_popen(*args: Any, **kwargs: Any) -> Any:
        manager.cancel_current()
        proc = mocker.MagicMock()
        proc.__enter__.return_value = proc
        proc.stdin = mocker.MagicMock()
        proc.stdin.closed = False
        proc.returncode = 0
        proc.wait.return_value = 0
        return proc

    mocker.patch("subprocess.Popen", side_effect=mock_popen)

    # Act
    manager.enqueue(
        ranges=[RangeFrame(start=0, end=5)],
        voutput=sample_voutput_proxy,
        dest_files=[dummy_file],
        ffmpeg_args=[],
        fmt="h264",
    )

    qtbot.waitUntil(lambda: not dummy_file.exists(), timeout=3000)

    # Assert
    assert not dummy_file.exists()
    assert any("Export cancelled" in msg for msg in status_messages)
    manager.shutdown(timeout=1.0)


def test_export_clip_pipes_frame_chunks_to_stdin(
    tmp_path: Path,
    qtbot: QtBot,
    mocker: MockerFixture,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
) -> None:
    # Arrange
    out_file = tmp_path / "out.mp4"

    written_bytes = bytearray()
    mock_stdin = mocker.MagicMock()
    mock_stdin.write.side_effect = lambda b: written_bytes.extend(b)
    mock_stdin.closed = False

    mock_proc = mocker.MagicMock()
    mock_proc.__enter__.return_value = mock_proc
    mock_proc.stdin = mock_stdin
    mock_proc.returncode = 0
    mock_proc.wait.return_value = 0

    mocker.patch("subprocess.Popen", return_value=mock_proc)

    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    # Real in-memory 4x2 GRAY8 clip filled with 'A' (65)
    real_clip = core.std.BlankClip(width=4, height=2, length=1, format=vs.GRAY8, color=65, fpsnum=24, fpsden=1)
    voutput_proxy = make_voutput_proxy(make_video_output(clip=real_clip, num_frames=1))

    # Act
    manager.enqueue(
        ranges=[RangeFrame(start=0, end=1)],
        voutput=voutput_proxy,
        dest_files=[out_file],
        ffmpeg_args=[],
        fmt="h264",
    )

    # Assert: wait until export completes successfully
    qtbot.waitUntil(lambda: any("Successfully exported" in msg for msg in status_messages), timeout=3000)
    assert b"AAAA" in bytes(written_bytes)
    assert any("Successfully exported 1 clip(s)." in msg for msg in status_messages)

    manager.shutdown(timeout=1.0)


def test_export_clip_ffmpeg_error(
    tmp_path: Path,
    qtbot: QtBot,
    mocker: MockerFixture,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
    sample_voutput_proxy: VideoOutputProxy,
) -> None:
    # Arrange
    out_file = tmp_path / "out.mp4"

    def mock_popen(cmd: Any, **kwargs: Any) -> Any:
        stderr_file = kwargs.get("stderr")
        if stderr_file:
            stderr_file.write(b"Error initializing encoder: invalid parameter\n")
        proc = mocker.MagicMock()
        proc.__enter__.return_value = proc
        proc.stdin = mocker.MagicMock()
        proc.stdin.closed = False
        proc.returncode = 1
        proc.wait.return_value = 1
        return proc

    mocker.patch("subprocess.Popen", side_effect=mock_popen)

    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    # Act
    manager.enqueue(
        ranges=[RangeFrame(start=0, end=1)],
        voutput=sample_voutput_proxy,
        dest_files=[out_file],
        ffmpeg_args=[],
        fmt="h264",
    )

    # Assert: worker catches error and emits status message
    qtbot.waitUntil(lambda: any("Export failed for out.mp4" in msg for msg in status_messages), timeout=3000)
    assert any(
        "Export failed for out.mp4: FFmpeg returned exit code 1: Error initializing encoder: invalid parameter" in msg
        for msg in status_messages
    )
    assert not out_file.exists()

    manager.shutdown(timeout=1.0)


# End-to-End Integration Tests (Real FFmpeg + In-Memory Video Node)
@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg binary not found")
def test_e2e_export_h264(
    tmp_path: Path,
    qtbot: QtBot,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
) -> None:
    dest_file = tmp_path / "e2e_out.mp4"

    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    voutput_proxy = make_voutput_proxy(make_video_output(num_frames=12, color_family=vs.RGB))

    manager.enqueue(
        ranges=[RangeFrame(start=0, end=5)],
        voutput=voutput_proxy,
        dest_files=[dest_file],
        ffmpeg_args=H264_ARGS,
        fmt="h264",
    )

    qtbot.waitUntil(lambda: "Successfully exported 1 clip(s)." in status_messages, timeout=5000)
    manager.shutdown(timeout=5.0)

    assert dest_file.is_file()
    assert dest_file.stat().st_size > 0


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg binary not found")
def test_e2e_export_ffv1(
    tmp_path: Path,
    qtbot: QtBot,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
) -> None:
    dest_file = tmp_path / "e2e_out.mkv"

    status_messages = list[str]()
    export_harness.statusMessage.connect(status_messages.append)

    voutput_proxy = make_voutput_proxy(make_video_output(num_frames=12, color_family=vs.YUV))

    manager.enqueue(
        ranges=[RangeFrame(start=0, end=5)],
        voutput=voutput_proxy,
        dest_files=[dest_file],
        ffmpeg_args=FFV1_ARGS,
        fmt="ffv1",
    )

    qtbot.waitUntil(lambda: "Successfully exported 1 clip(s)." in status_messages, timeout=5000)
    manager.shutdown(timeout=5.0)

    assert dest_file.is_file()
    assert dest_file.stat().st_size > 0


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg binary not found")
def test_e2e_export_cancel_kills_process_and_cleans_file(
    tmp_path: Path,
    qtbot: QtBot,
    mocker: MockerFixture,
    manager: ExportQueueManager,
    export_harness: PluginTestHarness,
) -> None:
    dest_file = tmp_path / "e2e_cancelled.mp4"

    # 200 frames to ensure FFmpeg is actively encoding when cancelled
    voutput_proxy = make_voutput_proxy(make_video_output(num_frames=200, color_family=vs.RGB))

    popen_spy = mocker.spy(subprocess, "Popen")

    manager.enqueue(
        ranges=[RangeFrame(start=0, end=199)],
        voutput=voutput_proxy,
        dest_files=[dest_file],
        ffmpeg_args=H264_ARGS,
        fmt="h264",
    )

    # Wait until FFmpeg process is spawned
    qtbot.waitUntil(lambda: popen_spy.call_count > 0, timeout=3000)

    # Cancel active export
    manager.cancel_current(wait=True, timeout=3.0)
    manager.shutdown(timeout=3.0)

    # Invariant: partial output must be unlinked
    assert not dest_file.exists()
