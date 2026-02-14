import re
from datetime import datetime, timedelta
from fractions import Fraction
from logging import getLogger
from math import ceil
from pathlib import Path

from .api import Parser
from .models import RangeFrame, RangeTime, SceneRow

logger = getLogger(__name__)


class AssParser(Parser):
    filter = Parser.FileFilter("Aegisub Advanced SSA subtitles", "ass")

    def parse(self, path: Path, fps: Fraction) -> SceneRow:
        try:
            with path.open(encoding="utf-8-sig") as file:
                text = file.read()
        except ValueError:
            raise ValueError(f"Could not read file or file is empty: {path}")

        ranges = list[RangeFrame]()

        for start_ts, end_ts, txt in re.findall(
            r"^Dialogue:\s\d+,([^,]+),([^,]+),(?:[^,]*,){6}(.*)$",
            text,
            re.MULTILINE,
        ):
            start_dt = datetime.strptime(start_ts, "%H:%M:%S.%f")
            end_dt = datetime.strptime(end_ts, "%H:%M:%S.%f")

            start_seconds = timedelta(
                hours=start_dt.hour,
                minutes=start_dt.minute,
                seconds=start_dt.second,
                microseconds=start_dt.microsecond,
            ).total_seconds()
            end_seconds = timedelta(
                hours=end_dt.hour,
                minutes=end_dt.minute,
                seconds=end_dt.second,
                microseconds=end_dt.microsecond,
            ).total_seconds()

            # formula is from videotimestamps with a rounding method of "round"
            # https://github.com/moi15moi/VideoTimestamps/blob/9d8259a94d069d7f85d6ab502b6ded3bfb25145a/video_timestamps/fps_timestamps.py#L65-L85
            start_frame = ceil(((ceil(start_seconds * 1000) - 0.5) / 1000) * fps + 1) - 1
            end_frame = ceil(((ceil(end_seconds * 1000) - 0.5) / 1000) * fps) - 1

            ranges.append(RangeFrame(start=start_frame, end=end_frame, label=txt))

        return SceneRow(color=self.get_color(), name=path.stem, ranges=ranges)


class OGMParser(Parser):
    filter = Parser.FileFilter("Ogg Media (OGM) Chapters", "txt")

    def parse(self, path: Path, fps: Fraction) -> SceneRow:
        pattern = re.compile(
            r"^\s*(CHAPTER\d+)\s*=\s*(\d+):(\d+):(\d+(?:\.\d+)?)\s*[\r\n]+"
            r"\s*\1NAME\s*=\s*(.*)",
            re.MULTILINE,
        )

        ranges = list[RangeTime]()

        for match in pattern.finditer(path.read_text("utf-8")):
            hours, minutes, seconds_str, name = match.group(2, 3, 4, 5)

            ranges.append(
                RangeTime(
                    start=timedelta(hours=int(hours), minutes=int(minutes), seconds=float(seconds_str)),
                    label=name.strip(),
                )
            )

        return SceneRow(color=self.get_color(), name=path.stem, ranges=ranges)


internal_parsers: list[Parser] = [
    AssParser(),
    OGMParser(),
]

# "Matroska XML Chapters (*.xml)": import_matroska_xml_chapters,
# "Wobbly File (*.wob)": import_wobbly,
# "Wobbly Sections (*.txt)"
# "x264/x265 QP File (*.qp *.txt)": import_qp,
# "XviD Log (*.txt)": import_xvid,
# "VSEdit Bookmarks (*.bookmarks)"
