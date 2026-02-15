from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from .specs import hookimpl

if TYPE_CHECKING:
    from PySide6.QtGui import QColor

    from .models import SceneRow

__all__ = ["Parser", "hookimpl"]


class Parser(ABC):
    class FileFilter(NamedTuple):
        """Named tuple representing a file filter for dialogs."""

        label: str
        """The display label for the filter."""
        suffix: str | Sequence[str]
        """The file extension suffix."""

    filter: ClassVar[FileFilter]

    @abstractmethod
    def parse(self, path: Path, fps: Fraction) -> SceneRow | Sequence[SceneRow]: ...

    if TYPE_CHECKING:

        def get_color(self) -> QColor: ...
