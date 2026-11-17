from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import timedelta
from typing import Annotated, Any, final, override
from uuid import UUID, uuid4

from jetpytools import fallback
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_serializer, model_validator
from PySide6.QtGui import QColor

from vsview.api import Time, VideoOutputProxy

from .constants import PLUGIN_IDENTIFIER


class UUIDModel(BaseModel):
    id: UUID = Field(default_factory=uuid4, repr=False, init=False)

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @override
    def __eq__(self, other: object) -> bool:
        return self.id == other.id if isinstance(other, UUIDModel) else NotImplemented


class AbstractRange[T](ABC, UUIDModel):
    """
    Base class for a scening range, which can be defined in frames or timestamps.

    A range consists of a start point, an optional end point (defaults to start), and an optional label.
    """

    start: T
    """The start point of the range."""
    end: T | None = None
    """The inclusive end: the last frame, or its start time for time ranges. If None, the range is a single point."""
    label: str = ""
    """An optional description for this range."""

    @abstractmethod
    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]:
        """
        Convert the range to a tuple of (start_frame, end_frame).

        Args:
            v: The video output proxy used for conversion.

        Returns:
            tuple[int, int]: The start and end frames.
        """
        ...

    @abstractmethod
    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]:
        """
        Convert the range to a tuple of (Time, Time).

        Args:
            v: The video output proxy used for conversion.

        Returns:
            tuple[Time, Time]: The start and end times.
        """
        ...

    @abstractmethod
    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None:
        """
        Update the range boundaries from frame numbers.

        Args:
            s: The new start frame.
            e: The new end frame.
            v: The video output proxy used for conversion.
        """
        ...

    @abstractmethod
    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None:
        """
        Update the range boundaries from timedelta objects.

        Args:
            s: The new start time.
            e: The new end time.
            v: The video output proxy used for conversion.
        """
        ...

    def to_tuple(self) -> tuple[T, T]:
        """
        Return a tuple of (start, end), where end defaults to start if None.
        """
        return self.start, fallback(self.end, self.start)


class RangeFrame(AbstractRange[int], UUIDModel):
    """A range defined by frame numbers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def _transform_input(cls, data: Any) -> Any:
        if isinstance(data, int):
            return {"start": data}
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            return {"start": data[0], "end": data[1]}
        return data

    @override
    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]:
        return self.start, (self.end if self.end is not None else self.start)

    @override
    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]:
        s, e = self.as_frames(v)
        return v.frame_to_time(s), v.frame_to_time(e)

    @override
    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = s
        if e is not None:
            self.end = e

    @override
    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = v.time_to_frame(s)
        if e is not None:
            self.end = v.time_to_frame(e)


class RangeTime(AbstractRange[timedelta], UUIDModel):
    """A range defined by timestamps (timedelta)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def _transform_input(cls, data: Any) -> Any:
        def _to_td(ts: Any) -> timedelta:
            if isinstance(ts, timedelta):
                return ts
            h, m, s = str(ts).split(":")
            return timedelta(hours=int(h), minutes=int(m), seconds=float(s))

        if isinstance(data, (str, timedelta)):
            return {"start": _to_td(data)}
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            return {"start": _to_td(data[0]), "end": _to_td(data[1])}
        return data

    @override
    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]:
        s, e = self.as_times(v)
        return v.time_to_frame(s), v.time_to_frame(e)

    @override
    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]:
        s = self.start
        e = self.end if self.end is not None else s

        return Time(seconds=s.total_seconds()), Time(seconds=e.total_seconds())

    @override
    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = v.frame_to_time(s)
        if e is not None:
            self.end = v.frame_to_time(e)

    @override
    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = s
        if e is not None:
            self.end = e


@final
class UnifiedRange:
    """A helper wrapper providing a unified interface for frame and time-based ranges."""

    def __init__(
        self,
        r: RangeFrame | RangeTime,
        frame_to_time: Callable[[int], Time],
        time_to_frame: Callable[[timedelta], int],
    ) -> None:
        self._r = r
        self._frame_to_time = frame_to_time
        self._time_to_frame = time_to_frame
        self.label = self._r.label

    def as_frames(self) -> tuple[int, int]:
        """Convert the range to a tuple of (start_frame, end_frame). The end frame is inclusive."""
        if isinstance(self._r, RangeFrame):
            return self._r.to_tuple()

        s, e = self._r.to_tuple()

        return self._time_to_frame(s), self._time_to_frame(e)

    def as_times(self) -> tuple[Time, Time]:
        """Convert the range to a tuple of (Time, Time). The end time is the start time of the last frame."""
        if isinstance(self._r, RangeTime):
            s, e = self._r.to_tuple()
            return Time(seconds=s.total_seconds()), Time(seconds=e.total_seconds())

        s, e = self._r.to_tuple()

        return self._frame_to_time(s), self._frame_to_time(e)


class SceneRow(UUIDModel):
    """Represents a collection of ranges grouped under a single name and color."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    color: Annotated[QColor, BeforeValidator(lambda v: v if isinstance(v, QColor) else QColor(v))]
    name: str
    checked_outputs: set[int] = Field(default_factory=set)
    display: bool = True

    ranges: list[RangeFrame | RangeTime] | list[RangeFrame] | list[RangeTime] = Field(default_factory=list)

    @property
    def notch_id(self) -> str:
        return ".".join([PLUGIN_IDENTIFIER, str(self.id)])

    @field_serializer("color")
    def serialize_color(self, color: QColor) -> str:
        return color.name()
