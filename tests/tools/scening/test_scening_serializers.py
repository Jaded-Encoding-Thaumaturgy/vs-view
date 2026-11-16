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
from vsview.app.tools.scening.parsers import PythonListTimestampsParser, QPFileParser
from vsview.app.tools.scening.serializer import PythonListTimestampsSerializer, QPFileSerializer
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
