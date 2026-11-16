from __future__ import annotations

import io
from datetime import timedelta
from fractions import Fraction
from types import MethodType
from typing import Any

import pytest
from PySide6.QtGui import QColor

from vsview.app.tools.scening.api import Parser, Serializer
from vsview.app.tools.scening.models import RangeFrame, RangeTime, UnifiedRange
from vsview.app.tools.scening.parsers import OGMParser, PythonListTimestampsParser, QPFileParser
from vsview.app.tools.scening.serializer import OGMSerializer, PythonListTimestampsSerializer, QPFileSerializer
from vsview.types import Time

pytestmark = [pytest.mark.unit]

FPS_24 = Fraction(24000, 1001)


def _frame_to_time(n: int) -> Time:
    return Time(seconds=float(n / FPS_24))


def _time_to_frame(t: timedelta) -> int:
    return round(t.total_seconds() * FPS_24)


def serialize(serializer: Serializer, ranges: list[RangeFrame | RangeTime]) -> bytes:
    buffer = io.BytesIO()
    serializer.serialize(buffer, iter([UnifiedRange(r, _frame_to_time, _time_to_frame) for r in ranges]))
    return buffer.getvalue()


def parse(parser: Parser, data: bytes) -> Any:
    setattr(parser, "get_color", MethodType(lambda _: QColor("#ff0000"), parser))
    return parser.parse(io.BytesIO(data), "x", FPS_24)


class TestPythonListTimestampsSerializer:
    def test_writes_milliseconds_with_three_digits(self) -> None:
        ranges: list[RangeFrame | RangeTime] = [
            RangeTime(start=timedelta(seconds=1.5), end=timedelta(seconds=2.25)),
            RangeTime(start=timedelta(hours=1, minutes=2, seconds=3, milliseconds=7)),
        ]

        out = serialize(PythonListTimestampsSerializer(), ranges)

        assert out == b"[('00:00:01.500', '00:00:02.250'), ('01:02:03.007', '01:02:03.007')]"

    def test_round_trips_through_its_parser(self) -> None:
        ranges: list[RangeFrame | RangeTime] = [
            RangeTime(start=timedelta(seconds=1.5), end=timedelta(seconds=2.25)),
            RangeTime(start=timedelta(minutes=59, seconds=59, milliseconds=999)),
        ]

        scene = parse(PythonListTimestampsParser(), serialize(PythonListTimestampsSerializer(), ranges))

        assert [(r.start, r.end) for r in scene.ranges] == [
            (timedelta(seconds=1.5), timedelta(seconds=2.25)),
            (timedelta(minutes=59, seconds=59, milliseconds=999), timedelta(minutes=59, seconds=59, milliseconds=999)),
        ]


class TestQPFileSerializer:
    @pytest.mark.parametrize(
        "labels",
        [
            pytest.param(["", "", ""], id="no-labels"),
            pytest.param(["Intro", "Ep", "Outro"], id="labels"),
        ],
    )
    def test_round_trips_frames_through_its_parser(self, labels: list[str]) -> None:
        ranges: list[RangeFrame | RangeTime] = [
            RangeFrame(start=frame, label=label) for frame, label in zip([0, 100, 200], labels)
        ]

        scene = parse(QPFileParser(), serialize(QPFileSerializer(), ranges))

        assert [r.start for r in scene.ranges] == [0, 100, 200]

    @pytest.mark.parametrize("label", ["Intro", "12", "-3"])
    def test_does_not_write_the_label(self, label: str) -> None:
        ranges: list[RangeFrame | RangeTime] = [RangeFrame(start=24, label=label), RangeFrame(start=48)]

        out = serialize(QPFileSerializer(), ranges)

        assert out.decode().splitlines() == ["24 K", "48 K"]


class TestOGMSerializer:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            pytest.param(
                "a\nCHAPTER09=00:00:09.000\nCHAPTER09NAME=evil", "a CHAPTER09=00:00:09.000 CHAPTER09NAME=evil", id="lf"
            ),
            pytest.param("Part\r\nOne", "Part One", id="crlf"),
            pytest.param("Part\rOne", "Part One", id="cr"),
            pytest.param("Plain", "Plain", id="no-line-break"),
        ],
    )
    def test_writes_each_label_on_one_line(self, label: str, expected: str) -> None:
        ranges: list[RangeFrame | RangeTime] = [RangeTime(start=timedelta(seconds=1), label=label)]

        out = serialize(OGMSerializer(), ranges)

        assert out.decode().splitlines() == ["CHAPTER01=00:00:01.000", f"CHAPTER01NAME={expected}"]

    def test_label_with_line_break_round_trips_as_one_chapter(self) -> None:
        ranges: list[RangeFrame | RangeTime] = [
            RangeTime(start=timedelta(seconds=1), label="a\nCHAPTER09=00:00:09.000\nCHAPTER09NAME=evil")
        ]

        scene = parse(OGMParser(), serialize(OGMSerializer(), ranges))

        assert [(r.start, r.label) for r in scene.ranges] == [
            (timedelta(seconds=1), "a CHAPTER09=00:00:09.000 CHAPTER09NAME=evil")
        ]
