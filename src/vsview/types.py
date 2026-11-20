from __future__ import annotations

import os
import shutil
from datetime import timedelta
from logging import getLogger
from typing import Any, Self, overload

from jetpytools import SPath, cround
from PySide6.QtCore import QTime

logger = getLogger(__name__)


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


class VPath(SPath):
    def safe_stat(self, *, follow_symlinks: bool = True) -> os.stat_result | None:
        try:
            return super().stat(follow_symlinks=follow_symlinks)
        except OSError:
            return None

    def safe_read_bytes(self) -> bytes | None:
        try:
            return self.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            logger.warning("Couldn't read the settings file %s", self, exc_info=True)
            return None

    def save_as_backup(self) -> None:
        backup_path = self.with_name(f"{self.name}.bak")

        try:
            shutil.copy2(self, backup_path)
        except OSError:
            logger.exception("Failed to back up the settings file %s", self)
        else:
            logger.warning("Saved a copy of the unreadable settings file to %s", backup_path)

    @overload
    def atomic_write(self, data: bytes) -> None: ...
    @overload
    def atomic_write(self, data: str, encoding: str = "utf-8", newline: str | None = None) -> None: ...
    def atomic_write(self, *args: Any, **kwargs: Any) -> None:
        from .app.utils import atomic_write

        atomic_write(self, *args, **kwargs)
