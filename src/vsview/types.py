from __future__ import annotations

from datetime import timedelta
from typing import Self

from jetpytools import cround
from PySide6.QtCore import QTime


class Frame(int):
    """Frame number type."""


class Time(timedelta):
    """Time type."""

    def to_qtime(self) -> QTime:
        """Convert a Time object to a QTime object."""
        total_ms = cround(self.total_seconds() * 1000)
        return QTime.fromMSecsSinceStartOfDay(total_ms)

    def to_ts(self, fmt: str = "{H:02d}:{M:02d}:{S:02d}.{ms:03d}") -> str:
        """Formats a timedelta object using standard Python formatting syntax."""
        total_ms = cround(self.total_seconds() * 1000)
        total_seconds, milliseconds = divmod(total_ms, 1000)
        days, remainder_seconds = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return fmt.format(
            D=days,
            H=hours,
            M=minutes,
            S=seconds,
            ms=milliseconds,
            us=self.microseconds,
            th=total_seconds // 3600,
            tm=total_seconds // 60,
            ts=total_seconds,
        )

    @classmethod
    def from_qtime(cls, qtime: QTime) -> Self:
        """Convert a QTime object to a Time object."""
        return cls(milliseconds=qtime.msecsSinceStartOfDay())
