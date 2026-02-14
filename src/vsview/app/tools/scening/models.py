from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from jetpytools import fallback
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_serializer
from PySide6.QtGui import QColor

from vsview.api import Time, VideoOutputProxy


class UUIDModel(BaseModel):
    id: UUID = Field(default_factory=uuid4, repr=False, init=False)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return self.id == other.id if isinstance(other, UUIDModel) else NotImplemented


class AbstractRange[T](ABC, UUIDModel):
    start: T
    end: T | None = None
    label: str = ""

    @abstractmethod
    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]: ...
    @abstractmethod
    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]: ...
    @abstractmethod
    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None: ...
    @abstractmethod
    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None: ...

    def to_tuple(self) -> tuple[T, T]:
        return self.start, fallback(self.end, self.start)


class RangeFrame(AbstractRange[int], UUIDModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]:
        return self.start, (self.end if self.end is not None else self.start)

    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]:
        s, e = self.as_frames(v)
        return v.frame_to_time(s), v.frame_to_time(e)

    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = s
        if e is not None:
            self.end = e

    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = v.time_to_frame(s)
        if e is not None:
            self.end = v.time_to_frame(e)


class RangeTime(AbstractRange[timedelta], UUIDModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def as_frames(self, v: VideoOutputProxy) -> tuple[int, int]:
        s, e = self.as_times(v)
        return v.time_to_frame(s), v.time_to_frame(e)

    def as_times(self, v: VideoOutputProxy) -> tuple[Time, Time]:
        s = self.start
        e = self.end if self.end is not None else s

        return Time(seconds=s.seconds), Time(seconds=e.seconds)

    def from_frames(self, s: int | None, e: int | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = v.frame_to_time(s)
        if e is not None:
            self.end = v.frame_to_time(e)

    def from_times(self, s: timedelta | None, e: timedelta | None, v: VideoOutputProxy) -> None:
        if s is not None:
            self.start = s
        if e is not None:
            self.end = e


class SceneRow(UUIDModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    color: Annotated[QColor, BeforeValidator(lambda v: v if isinstance(v, QColor) else QColor(v))]
    name: str
    checked_outputs: set[int] = Field(default_factory=set)
    display: bool = True

    ranges: list[RangeFrame | RangeTime] | list[RangeFrame] | list[RangeTime] = Field(default_factory=list)

    @property
    def notch_id(self) -> str:
        from .plugin import PLUGIN_IDENTIFIER

        return ".".join([PLUGIN_IDENTIFIER, str(self.id)])

    @field_serializer("color")
    def serialize_color(self, color: QColor) -> str:
        return color.name()
