from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import vapoursynth as vs
import vsengine.video
from vsengine import Cancelled

from vsview.api import PluginAPI, PluginSettings, VideoOutputProxy

from .models import AbstractRange

if TYPE_CHECKING:
    from .plugin import GlobalSettings, LocalSettings

logger = getLogger(__name__)


@dataclass
class ExportJob:
    ranges: list[AbstractRange[Any]]
    voutput: VideoOutputProxy
    dest_files: list[Path]
    ffmpeg_args: list[str]
    fmt: Literal["h264", "ffv1"]
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)


class ExportQueueManager:
    def __init__(self, api: PluginAPI, settings: PluginSettings[GlobalSettings, LocalSettings]) -> None:
        self.api = api
        self.settings = settings
        self._queue = queue.Queue[ExportJob | None]()
        self._current_job: ExportJob | None = None
        self._current_proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def enqueue(
        self,
        ranges: list[AbstractRange[Any]],
        voutput: VideoOutputProxy,
        dest_files: list[Path],
        ffmpeg_args: list[str],
        fmt: Literal["h264", "ffv1"],
    ) -> None:
        self._queue.put(ExportJob(ranges, voutput, dest_files, ffmpeg_args, fmt))
        self._ensure_worker_started()
        self.api.statusMessage.emit(f"Queued export of {len(dest_files)} clip(s). Queue size: {self._queue.qsize()}")

    def cancel_current(self, *, wait: bool = False, timeout: float = 3.0) -> None:
        job_to_wait: ExportJob | None = None

        with self._lock:
            if self._current_job:
                self._current_job.cancel_event.set()
                job_to_wait = self._current_job
            if self._current_proc:
                with suppress(OSError):
                    self._current_proc.kill()

        if wait and job_to_wait is not None:
            job_to_wait.done_event.wait(timeout=timeout)

    def cancel_all(self, *, wait: bool = True, timeout: float = 3.0) -> None:
        # Drain queued jobs
        while True:
            try:
                if (job := self._queue.get_nowait()) is not None:
                    job.cancel_event.set()
                    job.done_event.set()
                self._queue.task_done()
            except queue.Empty:
                break

        self.cancel_current(wait=wait, timeout=timeout)

    def shutdown(self, *, timeout: float = 3.0) -> None:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return

        self.cancel_all(wait=True, timeout=timeout)
        self._queue.put(None)

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _ensure_worker_started(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker_loop, name="ExportWorkerThread", daemon=True)
                self._thread.start()

    def _worker_loop(self) -> None:
        try:
            while True:
                if (job := self._queue.get()) is None:
                    self._queue.task_done()
                    break

                if job.cancel_event.is_set():
                    job.done_event.set()
                    self._queue.task_done()
                    continue

                with self._lock:
                    self._current_job = job

                try:
                    with self.api.blocker(), self.api.vs_context():
                        self._execute_job(job)
                except Exception:
                    logger.exception("Unexpected error during export job execution")
                finally:
                    job.done_event.set()
                    with self._lock:
                        self._current_job = None
                    self._queue.task_done()
        finally:
            with self._lock:
                self._thread = None

    def _execute_job(self, job: ExportJob) -> None:
        total = len(job.ranges)

        for i, (r, dest_file) in enumerate(zip(job.ranges, job.dest_files), 1):
            if job.cancel_event.is_set():
                dest_file.unlink(missing_ok=True)
                self.api.statusMessage.emit("Export cancelled.")
                return

            self.api.statusMessage.emit(f"Exporting clip {i}/{total}: {dest_file.name[:100]}...")

            s, e = r.as_frames(job.voutput)
            try:
                trimmed = job.voutput.vs_output.clip[s : e + (not self.settings.global_.exclusive)]
            except vs.Error as exc:
                logger.error("Failed to slice clip: %s", exc)
                continue

            cfmt = trimmed.format
            try:
                if job.fmt == "ffv1" and (cfmt.bits_per_sample > 16 or cfmt.sample_type == vs.FLOAT):
                    target_fmt = cfmt.replace(bits_per_sample=16, sample_type=vs.INTEGER)
                    logger.warning("Converting format %s to 16-bit integer (%s)", cfmt.name, target_fmt.name)
                    trimmed = trimmed.resize.Point(format=target_fmt)
                elif job.fmt == "h264":
                    if cfmt.color_family == vs.RGB:
                        target_fmt = cfmt.replace(color_family=vs.YUV, bits_per_sample=8, sample_type=vs.INTEGER)
                        logger.warning("Converting RGB format %s to %s", cfmt.name, target_fmt.name)
                        trimmed = trimmed.resize.Point(format=target_fmt, matrix_s="709")
                    elif cfmt.bits_per_sample > 8:
                        target_fmt = cfmt.replace(bits_per_sample=8, sample_type=vs.INTEGER)
                        logger.warning("Converting format %s to 8-bit (%s)", cfmt.name, target_fmt.name)
                        trimmed = trimmed.resize.Point(format=target_fmt)
            except vs.Error:
                logger.exception("Failed to convert format for output %s", job.voutput.vs_index)
                continue

            try:
                self._export_clip(job, trimmed, dest_file, job.ffmpeg_args)
            except Cancelled:
                self.api.statusMessage.emit(f"Export cancelled for {dest_file.name}.")
                dest_file.unlink(missing_ok=True)
                return
            except Exception as exc:
                logger.exception("Export failed for %s", dest_file)
                dest_file.unlink(missing_ok=True)
                self.api.statusMessage.emit(f"Export failed for {dest_file.name}: {exc}")
                return

        logger.info("Successfully exported %s clip(s).", total)
        self.api.statusMessage.emit(f"Successfully exported {total} clip(s).")

    def _export_clip(self, job: ExportJob, clip: vs.VideoNode, destination_file: Path, ffmpeg_args: list[str]) -> None:
        destination_file.parent.mkdir(parents=True, exist_ok=True)

        is_y4m = clip.format.color_family in (vs.ColorFamily.YUV, vs.ColorFamily.GRAY)
        total = clip.num_frames

        if is_y4m:
            input_args = ["-f", "yuv4mpegpipe", "-i", "-"]
        else:
            pix_fmt = "gbrp" if clip.format.bits_per_sample == 8 else f"gbrp{clip.format.bits_per_sample}le"
            input_args = [
                "-f",
                "rawvideo",
                "-pix_fmt",
                pix_fmt,
                "-s",
                f"{clip.width}x{clip.height}",
                "-r",
                f"{clip.fps_num}/{clip.fps_den}",
                "-i",
                "-",
            ]
            clip = clip.std.ShufflePlanes([1, 2, 0], clip.format.color_family)

        cmd: list[Any] = [
            self.settings.global_.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            *input_args,
            *ffmpeg_args,
            destination_file,
        ]

        with (
            tempfile.TemporaryFile(mode="w+b") as stderr_file,
            subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file) as proc,
            self._bind_proc(proc),
        ):
            output_err: Exception | None = None
            try:
                assert proc.stdin is not None
                if is_y4m:
                    proc.stdin.write(_get_y4m_header(clip))

                logger.debug("Exporting %s %s/%s", destination_file.name, 0, total)
                for idx, frame in enumerate(vsengine.video.frames(clip, prefetch=2)):
                    if job.cancel_event.is_set():
                        raise Cancelled
                    if (i := idx + 1) % round(clip.fps) == 0 or i == total:
                        logger.debug("Exporting %s %s/%s", destination_file.name, i, total)

                    if is_y4m:
                        proc.stdin.write(b"FRAME\n")
                    for chunk in frame.readchunks():
                        proc.stdin.write(chunk)
            except (OSError, vs.Error, ValueError, RuntimeError) as exc:
                if job.cancel_event.is_set():
                    raise Cancelled from None
                output_err = exc
            finally:
                if proc.stdin and not proc.stdin.closed:
                    with suppress(OSError):
                        proc.stdin.close()

            proc.wait()

            if job.cancel_event.is_set():
                raise Cancelled

            if proc.returncode != 0:
                stderr_file.seek(0)
                err_text = stderr_file.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"FFmpeg returned exit code {proc.returncode}: {err_text}") from output_err

            if output_err is not None:
                raise output_err

    @contextmanager
    def _bind_proc(self, proc: subprocess.Popen[bytes]) -> Generator[None]:
        with self._lock:
            self._current_proc = proc

        try:
            yield
        finally:
            with self._lock:
                self._current_proc = None


H264_ARGS = ["-c:v", "libx264", "-crf", "18.67", "-preset", "medium"]
FFV1_ARGS = ["-c:v", "ffv1", "-level", "3"]


def _get_y4m_header(clip: vs.VideoNode) -> bytes:
    fmt = clip.format
    if fmt.num_planes == 1:
        y4mformat = "mono"
    else:
        match fmt.subsampling_w, fmt.subsampling_h:
            case 1, 1:
                y4mformat = "420"
            case 1, 0:
                y4mformat = "422"
            case 0, 0:
                y4mformat = "444"
            case 2, 2:
                y4mformat = "410"
            case 2, 0:
                y4mformat = "411"
            case 0, 1:
                y4mformat = "440"
            case _:
                y4mformat = "420"

    if fmt.bits_per_sample > 8:
        y4mformat += f"p{fmt.bits_per_sample}"

    return (
        f"YUV4MPEG2 C{y4mformat} W{clip.width} H{clip.height} "
        f"F{clip.fps_num}:{clip.fps_den} Ip A0:0 XLENGTH={clip.num_frames}\n"
    ).encode("ascii")
