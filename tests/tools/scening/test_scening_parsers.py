from __future__ import annotations

import json
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from PySide6.QtGui import QColor

from vsview.app.tools.scening.models import RangeFrame, RangeTime, SceneRow
from vsview.app.tools.scening.parsers import (
    AssParser,
    MatroskaXMLParser,
    OGMParser,
    PythonListFramesParser,
    PythonListTimestampsParser,
    QPFileParser,
    WobblyParser,
    XvidLogParser,
)

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "scening"
FPS_24 = Fraction(24000, 1001)
FIXED_COLOR = QColor("#ff0000")


def _patch_parser(parser: Any) -> Any:
    setattr(parser, "get_color", MethodType(lambda _: FIXED_COLOR, parser))
    return parser


def parse(parser: Any, path: Path) -> Any:
    with path.open("rb") as f:
        return parser.parse(f, path.stem, FPS_24)


@pytest.fixture
def ass_p() -> AssParser:
    return _patch_parser(AssParser())


@pytest.fixture
def ogm_p() -> OGMParser:
    return _patch_parser(OGMParser())


@pytest.fixture
def xml_p() -> MatroskaXMLParser:
    return _patch_parser(MatroskaXMLParser())


@pytest.fixture
def xvid_p() -> XvidLogParser:
    return _patch_parser(XvidLogParser())


@pytest.fixture
def qp_p() -> QPFileParser:
    return _patch_parser(QPFileParser())


@pytest.fixture
def wob_p() -> WobblyParser:
    return _patch_parser(WobblyParser())


@pytest.fixture
def py_frames_p() -> PythonListFramesParser:
    return _patch_parser(PythonListFramesParser())


@pytest.fixture
def py_ts_p() -> PythonListTimestampsParser:
    return _patch_parser(PythonListTimestampsParser())


class TestAssParser:
    def test_parse_logic(self, ass_p: AssParser) -> None:
        result = parse(ass_p, FIXTURES / "test_ass.ass")
        assert result.name == "test_ass"
        assert len(result.ranges) == 1072
        assert all(isinstance(r, RangeFrame) for r in result.ranges)

        # Boundary checks
        first, last = result.ranges[0], result.ranges[-1]
        assert isinstance(first, RangeFrame)
        assert isinstance(last, RangeFrame)
        assert (first.start, first.end) == (2986, 3082)
        assert (last.start, last.end) == (140081, 140118)
        assert "ll Y A DES MILLIONS" in first.label
        assert result.color == FIXED_COLOR


class TestOGMParser:
    def test_parse_logic(self, ogm_p: OGMParser, tmp_path: Path) -> None:
        f = tmp_path / "test_ogm.txt"
        f.write_text(
            "CHAPTER01=00:00:00.000\nCHAPTER01NAME=Intro\nCHAPTER02=01:23:45.678\nCHAPTER02NAME=Ending\n",
            encoding="utf-8",
        )
        result = parse(ogm_p, f)
        assert len(result.ranges) == 2
        assert [(r.start, r.label) for r in result.ranges] == [
            (timedelta(0), "Intro"),
            (timedelta(hours=1, minutes=23, seconds=45, microseconds=678000), "Ending"),
        ]

    def test_parse_fixture(self, ogm_p: OGMParser) -> None:
        result = parse(ogm_p, FIXTURES / "test_ogm.txt")
        assert len(result.ranges) == 12
        assert [(r.start, r.label) for r in result.ranges] == [
            (timedelta(0), "intro"),
            (timedelta(seconds=432, microseconds=798000), "A"),
            (timedelta(seconds=971, microseconds=440000), "B"),
            (timedelta(seconds=1412, microseconds=654000), ""),
            (timedelta(seconds=1880, microseconds=756000), ""),
            (timedelta(seconds=2375, microseconds=745000), ""),
            (timedelta(seconds=3017, microseconds=860000), ""),
            (timedelta(seconds=3504, microseconds=231000), ""),
            (timedelta(seconds=3926, microseconds=274000), ""),
            (timedelta(seconds=4391, microseconds=103000), ""),
            (timedelta(seconds=4861, microseconds=876000), ""),
            (timedelta(seconds=5326, microseconds=537000), ""),
        ]

    def test_empty_file(self, ogm_p: OGMParser, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match=r"^Empty file$"):
            parse(ogm_p, f)


class TestMatroskaXMLParser:
    def test_single_edition(self, xml_p: MatroskaXMLParser) -> None:
        result = parse(xml_p, FIXTURES / "test_matroska_single.xml")
        assert isinstance(result, list)
        scene = result[0]
        assert scene.name == "test_matroska_single"
        assert len(scene.ranges) == 7
        assert (scene.ranges[0].start, scene.ranges[0].label) == (timedelta(0), "Chapter 01")
        assert scene.ranges[-1].start == timedelta(minutes=23, seconds=34, microseconds=998000)

    def test_multi_edition(self, xml_p: MatroskaXMLParser) -> None:
        scenes = parse(xml_p, FIXTURES / "test_matroska_multi.xml")
        assert isinstance(scenes, list)
        assert len(scenes) == 2
        assert [s.name for s in scenes] == ["test_matroska_multi (1001)", "test_matroska_multi (2002)"]
        assert [len(s.ranges) for s in scenes] == [2, 3]

    def test_edge_case_invalid_xml_raises(self, xml_p: MatroskaXMLParser, tmp_path: Path) -> None:
        f = tmp_path / "test.xml"
        f.write_text("not xml", encoding="utf-8")
        with pytest.raises(ValueError, match=r"Could not parse XML"):
            parse(xml_p, f)

    def test_edge_case_empty_chapters(self, xml_p: MatroskaXMLParser, tmp_path: Path) -> None:
        f = tmp_path / "test.xml"
        f.write_text("<Chapters></Chapters>", encoding="utf-8")
        result = parse(xml_p, f)
        assert isinstance(result, SceneRow)
        assert result.name == "test"
        assert result.ranges == []


class TestXvidLogParser:
    def test_parse_logic(self, xvid_p: XvidLogParser) -> None:
        result = parse(xvid_p, FIXTURES / "test_xvidlog.txt")
        assert len(result.ranges) == 432
        assert result.ranges[0].start == 0
        assert result.ranges[-1].start == 33976

    def test_parse_empty_xvid(self, xvid_p: XvidLogParser, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("# XviD 2pass stat file\n# comment\n", encoding="utf-8")
        result = parse(xvid_p, f)
        assert len(result.ranges) == 0


class TestQPFileParser:
    @pytest.mark.parametrize(
        ("filename", "count", "first_frame", "label"),
        [
            ("test_qpfile.txt", 76, 30, "-1"),
            ("test_qpfile_with_header.txt", 730, 0, "-1"),
        ],
    )
    def test_variations(self, qp_p: QPFileParser, filename: str, count: int, first_frame: int, label: str) -> None:
        result = parse(qp_p, FIXTURES / filename)
        assert len(result.ranges) == count
        assert (result.ranges[0].start, result.ranges[0].label) == (first_frame, label)

    def test_parse_empty_qp(self, qp_p: QPFileParser, tmp_path: Path) -> None:
        f = tmp_path / "empty.qp"
        f.write_text("", encoding="utf-8")
        result = parse(qp_p, f)
        assert len(result.ranges) == 0


class TestWobblyParser:
    def test_parse_fixture(self, wob_p: WobblyParser) -> None:
        results = parse(wob_p, FIXTURES / "test_wobbly.wob")
        assert isinstance(results, list)
        assert len(results) == 2

        [bookmarks] = [s for s in results if "(Bookmarks)" in s.name]
        assert len(bookmarks.ranges) == 93
        assert (bookmarks.ranges[0].start, bookmarks.ranges[0].label) == (29, "aliased desk")
        assert (bookmarks.ranges[-1].start, bookmarks.ranges[-1].label) == (36920, "newly kept")

        [sections] = [s for s in results if "(Sections)" in s.name]
        assert len(sections.ranges) == 367
        assert (sections.ranges[0].start, sections.ranges[0].end) == (0, 29)
        assert (sections.ranges[-1].start, sections.ranges[-1].end) == (36813, 36920)

    def test_label_formatting(self, wob_p: WobblyParser, tmp_path: Path) -> None:
        data = {
            "sections": [{"start": 0, "presets": ["a", "b"]}, {"start": 100, "preset": "c"}],
            "trim": [[0, 200]],
        }
        f = tmp_path / "labels.wob"
        f.write_text(json.dumps(data))
        res = parse(wob_p, f)
        assert isinstance(res, list)
        [sections] = [s for s in res if "(Sections)" in s.name]
        assert [r.label for r in sections.ranges] == ["a, b", "c"]

    def test_integer_format(self, wob_p: WobblyParser, tmp_path: Path) -> None:
        data = {"sections": [0, 50, 100], "trim": [[0, 150]]}
        f = tmp_path / "int.wob"
        f.write_text(json.dumps(data))
        res = parse(wob_p, f)
        assert isinstance(res, list)
        [sections] = [s for s in res if "(Sections)" in s.name]
        assert len(sections.ranges) == 3
        assert [r.start for r in sections.ranges] == [0, 50, 100]

    def test_errors(self, wob_p: WobblyParser, tmp_path: Path) -> None:
        f = tmp_path / "bad.wob"
        f.write_text("invalid")
        with pytest.raises(ValueError, match=r"^Could not parse Wobbly"):
            parse(wob_p, f)

        f2 = tmp_path / "empty.wob"
        f2.write_text("{}")
        with pytest.raises(ValueError, match=r"^Could not find any sections"):
            parse(wob_p, f2)


class TestPythonListFramesParser:
    def test_parse_logic(self, py_frames_p: PythonListFramesParser, tmp_path: Path) -> None:
        data = [30, (100, 200), (300, 400)]
        f = tmp_path / "test.txt"
        f.write_text(str(data))
        result = parse(py_frames_p, f)

        assert len(result.ranges) == 3
        assert result.ranges[0].start == 30
        assert (result.ranges[1].start, result.ranges[1].end) == (100, 200)
        assert (result.ranges[2].start, result.ranges[2].end) == (300, 400)

    def test_empty_file(self, py_frames_p: PythonListFramesParser, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        with pytest.raises(ValueError, match=r"^Empty file$"):
            parse(py_frames_p, f)


class TestPythonListTimestampsParser:
    def test_parse_logic(self, py_ts_p: PythonListTimestampsParser, tmp_path: Path) -> None:
        data = ["00:00:30.000000", ("00:01:00.000000", "00:02:00.000000")]
        f = tmp_path / "test.txt"
        f.write_text(str(data))
        result = parse(py_ts_p, f)

        assert len(result.ranges) == 2
        assert isinstance(result.ranges[0], RangeTime)
        assert result.ranges[0].start == timedelta(seconds=30)
        assert result.ranges[1].start == timedelta(minutes=1)
        assert result.ranges[1].end == timedelta(minutes=2)

    def test_empty_file(self, py_ts_p: PythonListTimestampsParser, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        with pytest.raises(ValueError, match=r"^Empty file$"):
            parse(py_ts_p, f)
