import ast
import json
import re
from bisect import bisect_left
from datetime import timedelta
from fractions import Fraction
from logging import getLogger
from math import ceil
from typing import Any, BinaryIO, override

from jetpytools import fallback

from .api import Parser, borrowed_text_wrapper
from .models import RangeFrame, RangeTime, SceneRow

logger = getLogger(__name__)


class AssParser(Parser):
    filter = Parser.FileFilter("Aegisub Advanced SSA subtitles", "ass")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        with borrowed_text_wrapper(io, encoding="utf-8-sig") as wrapper:
            text = wrapper.read()

        ranges = list[RangeFrame]()

        for matched in re.finditer(r"^Dialogue:\s\d+,([^,]+),([^,]+),(?:[^,]*,){6}(.*)$", text, re.MULTILINE):
            start_ts, end_ts, txt = matched.groups()

            h, m, s = start_ts.split(":")
            start_seconds = int(h) * 3600 + int(m) * 60 + float(s)

            h, m, s = end_ts.split(":")
            end_seconds = int(h) * 3600 + int(m) * 60 + float(s)

            # formula is from videotimestamps with a rounding method of "round"
            # https://github.com/moi15moi/VideoTimestamps/blob/9d8259a94d069d7f85d6ab502b6ded3bfb25145a/video_timestamps/fps_timestamps.py#L65-L85
            start_frame = ceil(((ceil(start_seconds * 1000) - 0.5) / 1000) * fps + 1) - 1
            end_frame = ceil(((ceil(end_seconds * 1000) - 0.5) / 1000) * fps) - 1

            ranges.append(RangeFrame(start=start_frame, end=end_frame, label=txt))

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


class OGMParser(Parser):
    filter = Parser.FileFilter("Ogg Media (OGM) Chapters", "txt")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        pattern = re.compile(
            r"^[ \t]*(CHAPTER\d+)[ \t]*=[ \t]*(\d+):(\d+):(\d+(?:\.\d+)?)[ \t]*[\r\n]+"
            r"[ \t]*\1NAME[ \t]*=[ \t]*(.*)",
            re.MULTILINE,
        )

        ranges = list[RangeTime]()

        with borrowed_text_wrapper(io) as wrapper:
            text = wrapper.read()

        if not text:
            raise ValueError("Empty file")

        for match in pattern.finditer(text):
            hours, minutes, seconds_str, name_val = match.group(2, 3, 4, 5)

            ranges.append(
                RangeTime(
                    start=timedelta(hours=int(hours), minutes=int(minutes), seconds=float(seconds_str)),
                    label=name_val.strip(),
                )
            )

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


class MatroskaXMLParser(Parser):
    filter = Parser.FileFilter("Matroska XML Chapters", "xml")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow | list[SceneRow]:
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(io)
            root = tree.getroot()
        except ET.ParseError:
            raise ValueError(f"Could not parse XML file: {name}")

        editions = root.findall(".//EditionEntry")

        # Fallback if no EditionEntry is found
        if not editions:
            return SceneRow(color=self.get_color(), name=name, ranges=self._parse_atoms(root))

        scenes = list[SceneRow]()
        for i, edition in enumerate(editions, 1):
            if not (ranges := self._parse_atoms(edition)):
                continue

            uid_node = edition.find("EditionUID")
            uid_str = f" ({uid_node.text})" if uid_node is not None and uid_node.text else f" (Edition {i})"

            # If only one edition, keep the simple name
            name_edition = name if len(editions) == 1 else f"{name}{uid_str}"
            scenes.append(SceneRow(color=self.get_color(), name=name_edition, ranges=ranges))

        return scenes

    def _parse_atoms(self, parent: Any) -> list[RangeTime]:
        ranges = list[RangeTime]()

        # Find all ChapterAtom nodes
        for atom in parent.findall(".//ChapterAtom"):
            time_node = atom.find("ChapterTimeStart")

            if time_node is None or time_node.text is None:
                continue

            try:
                # Format is HH:MM:SS.nnnnnnnnn
                # We split manually because datetime.strptime chokes on 9-digit nanoseconds
                parts = time_node.text.split(":")
                if len(parts) == 3:
                    start_dt = timedelta(hours=int(parts[0]), minutes=int(parts[1]), seconds=float(parts[2]))

                    label = ""
                    if (display_node := atom.find("ChapterDisplay")) is not None:
                        name_node = display_node.find("ChapterString")

                        if name_node is not None and name_node.text:
                            label = name_node.text

                    ranges.append(RangeTime(start=start_dt, label=label))
            except ValueError:
                continue

        return ranges


class XvidLogParser(Parser):
    filter = Parser.FileFilter("XviD Log", ["txt", "log"])

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        ranges = list[RangeFrame]()
        current_frame = 0

        with borrowed_text_wrapper(io, encoding="utf-8") as wrapper:
            lines = wrapper.read()

        for line in lines.splitlines():
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if line[0] == "i":
                ranges.append(RangeFrame(start=current_frame))

            current_frame += 1

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


class QPFileParser(Parser):
    filter = Parser.FileFilter("QP File", ["qp", "txt"])

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        ranges = list[RangeFrame]()

        with borrowed_text_wrapper(io, encoding="utf-8") as wrapper:
            text = wrapper.read()

        for matched in re.finditer(r"^(\d+)\s+[IK]\s+(-?\d+)", text, re.MULTILINE):
            try:
                frame = int(matched.group(1))
            except ValueError:
                continue

            ranges.append(RangeFrame(start=frame, label=str(matched.group(2))))

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


class WobblyParser(Parser):
    filter = Parser.FileFilter("Wobbly File", "wob")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow | list[SceneRow]:
        with borrowed_text_wrapper(io, encoding="utf-8") as wrapper:
            text = wrapper.read()

        try:
            data: dict[str, Any] = json.loads(text)
        except Exception as e:
            raise ValueError(f"Could not parse Wobbly file: {name}") from e

        decimations = sorted(data.get("decimated frames", []))
        scenes = list[SceneRow]()

        # Sections
        if sections := data.get("sections"):
            starts = list[int]()
            labels = list[str]()

            for s in sections:
                if isinstance(s, int):
                    starts.append(s)
                    labels.append("")
                elif isinstance(s, dict):
                    starts.append(s.get("start", 0))

                    preset = s.get("preset") or s.get("presets")
                    if isinstance(preset, list):
                        label = ", ".join(map(str, preset)) if preset else ""
                    else:
                        label = str(fallback(preset, ""))
                    labels.append(label)

            trim = data.get("trim", [[0, 0]])
            last_end = trim[0][1] if trim and isinstance(trim, list) and isinstance(trim[0], list) else starts[-1]
            ends = [*starts[1:], last_end]

            ranges = list[RangeFrame]()
            for start, end, label in zip(starts, ends, labels):
                if decimations:
                    start -= bisect_left(decimations, start)
                    end -= bisect_left(decimations, end)

                ranges.append(RangeFrame(start=start, end=end, label=label))

            scenes.append(SceneRow(color=self.get_color(), name=f"{name} (Sections)", ranges=ranges))

        # Bookmarks
        if bookmarks := data.get("user interface", {}).get("bookmarks"):
            bookmark_ranges = list[RangeFrame]()

            for b in bookmarks:
                if not isinstance(b, dict) or "frame" not in b:
                    continue

                frame = int(b["frame"])
                label = str(b.get("description", ""))

                if decimations:
                    frame -= bisect_left(decimations, frame)

                bookmark_ranges.append(RangeFrame(start=frame, label=label))

            if bookmark_ranges:
                scenes.append(SceneRow(color=self.get_color(), name=f"{name} (Bookmarks)", ranges=bookmark_ranges))

        if not scenes:
            raise ValueError(f"Could not find any sections nor bookmarks in Wobble file {name}")

        return scenes


class PythonListFramesParser(Parser):
    filter = Parser.FileFilter("Python List (Frames)", "txt")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        with borrowed_text_wrapper(io) as wrapper:
            text = wrapper.read().strip()

        if not text:
            raise ValueError("Empty file")

        try:
            data = ast.literal_eval(text)
        except Exception as e:
            raise ValueError(f"Could not parse Python List (Frames) file: {name}") from e

        ranges = list[RangeFrame]()

        for item in data:
            try:
                ranges.append(RangeFrame.model_validate(item))
            except Exception:
                logger.warning("Skipping invalid item in %s: %s", name, item)
                logger.debug("Full traceback:", exc_info=True)

        if not ranges:
            raise ValueError("Empty file")

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


class PythonListTimestampsParser(Parser):
    filter = Parser.FileFilter("Python List (Timestamps)", "txt")

    @override
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow:
        with borrowed_text_wrapper(io) as wrapper:
            text = wrapper.read().strip()

        if not text:
            raise ValueError("Empty file")

        try:
            data = ast.literal_eval(text)
        except Exception as e:
            raise ValueError(f"Could not parse Python List (Timestamps) file: {name}") from e

        ranges = list[RangeTime]()

        for item in data:
            try:
                ranges.append(RangeTime.model_validate(item))
            except Exception:
                logger.warning("Error parsing item in %s: %s", name, item)
                logger.debug("Full traceback:", exc_info=True)

        if not ranges:
            raise ValueError("Empty file")

        return SceneRow(color=self.get_color(), name=name, ranges=ranges)


internal_parsers: list[Parser] = [
    AssParser(),
    OGMParser(),
    MatroskaXMLParser(),
    XvidLogParser(),
    QPFileParser(),
    WobblyParser(),
    PythonListFramesParser(),
    PythonListTimestampsParser(),
]

# "Wobbly Sections (*.txt)"
# "VSEdit Bookmarks (*.bookmarks)"
