from __future__ import annotations

import subprocess
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import vapoursynth as vs
import vsengine.video
from vsengine import Cancelled

from vsview.api import PluginAPI, PluginSettings, VideoOutputProxy, run_in_background

from .models import AbstractRange

if TYPE_CHECKING:
    from .plugin import GlobalSettings, LocalSettings

logger = getLogger(__name__)


@dataclass
class ExportWorker:
    api: PluginAPI
    settings: PluginSettings[GlobalSettings, LocalSettings]

    def __post_init__(self) -> None:
        self.cancelled = False

    @run_in_background(name="ExportClip")
    def run(
        self,
        ranges: list[AbstractRange[Any]],
        voutput: VideoOutputProxy,
        dest_files: list[Path],
        ffmpeg_args: list[str],
        fmt: Literal["h264", "ffv1"],
    ) -> None:
        total = len(ranges)

        with self.api.blocker(), self.api.vs_context():
            for i, (r, dest_file) in enumerate(zip(ranges, dest_files), 1):
                if self.cancelled:
                    raise Cancelled

                self.api.statusMessage.emit(f"Exporting clip {i}/{total}: {dest_file.name[:100]}...")

                s, e = r.as_frames(voutput)
                try:
                    trimmed = voutput.vs_output.clip[s : e + (not self.settings.global_.exclusive)]
                except vs.Error as e:
                    logger.error(e)
                    continue

                cfmt = trimmed.format
                try:
                    if fmt == "ffv1" and (cfmt.bits_per_sample > 16 or cfmt.sample_type == vs.FLOAT):
                        target_fmt = cfmt.replace(bits_per_sample=16, sample_type=vs.INTEGER)
                        logger.warning("Converting format %s to 16-bit integer (%s)", cfmt.name, target_fmt.name)
                        trimmed = trimmed.resize.Point(format=target_fmt)
                    elif fmt == "h264":
                        if cfmt.color_family == vs.RGB:
                            target_fmt = cfmt.replace(color_family=vs.YUV, bits_per_sample=8, sample_type=vs.INTEGER)
                            logger.warning("Converting RGB format %s to %s", cfmt.name, target_fmt.name)
                            trimmed = trimmed.resize.Point(format=target_fmt, matrix_s="709")
                        elif cfmt.bits_per_sample > 8:
                            target_fmt = cfmt.replace(bits_per_sample=8, sample_type=vs.INTEGER)
                            logger.warning("Converting format %s to 8-bit (%s)", cfmt.name, target_fmt.name)
                            trimmed = trimmed.resize.Point(format=target_fmt)
                except vs.Error:
                    logger.exception("Failed to convert output n° %s", voutput.vs_index)
                    continue

                try:
                    self._export_clip(trimmed, dest_file, ffmpeg_args)
                except Exception as exc:
                    logger.exception("Export failed for %s", dest_file)
                    self.api.statusMessage.emit(f"Export failed for {dest_file.name}: {exc}")
                    return

            logger.info("Successfully exported %s clip(s).", total)
            self.api.statusMessage.emit(f"Successfully exported {total} clip(s).")

    def _export_clip(self, clip: vs.VideoNode, destination_file: Path, ffmpeg_args: list[str]) -> None:
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

        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as proc:
            assert proc.stdin is not None
            output_err: Exception | None = None
            try:
                if is_y4m:
                    proc.stdin.write(_get_y4m_header(clip))

                for idx, frame in enumerate(vsengine.video.frames(clip)):
                    if self.cancelled:
                        raise Cancelled
                    if (i := idx + 1) % round(clip.fps) == 0:
                        logger.debug("Exporting %s/%s", i, total)

                    if is_y4m:
                        proc.stdin.write(b"FRAME\n")
                    for plane in range(frame.format.num_planes):
                        proc.stdin.write(frame[plane])

            except (OSError, vs.Error, ValueError, RuntimeError) as exc:
                output_err = exc
            finally:
                if not proc.stdin.closed:
                    proc.stdin.close()

            _, stderr = proc.communicate()
            if proc.returncode != 0:
                err_text = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"FFmpeg returned exit code {proc.returncode}: {err_text}") from output_err
            if output_err is not None:
                raise output_err


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
