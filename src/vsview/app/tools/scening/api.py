from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from fractions import Fraction
from io import TextIOWrapper
from typing import TYPE_CHECKING, BinaryIO, ClassVar, NamedTuple

from .models import RangeFrame, RangeTime, SceneRow, UnifiedRange
from .specs import hookimpl

if TYPE_CHECKING:
    from PySide6.QtGui import QColor


__all__ = [
    "FileFilter",
    "Parser",
    "RangeFrame",
    "RangeTime",
    "SceneRow",
    "UnifiedRange",
    "borrowed_text_wrapper",
    "hookimpl",
]


class FileFilter(NamedTuple):
    """
    Metadata used to define file types in the scening tool's import/export dialogs.
    """

    label: str
    """The human-readable description of the file format (e.g., "Matroska XML Chapters")."""
    suffix: str | Sequence[str]
    """The file extension(s) without the leading dot (e.g., "xml" or ["txt", "log"])."""


_FileFilter = FileFilter


@contextmanager
def borrowed_text_wrapper(
    stream: BinaryIO,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> Iterator[TextIOWrapper]:
    """A context manager that wraps a binary stream into a text stream without closing it."""

    wrapper = TextIOWrapper(stream, encoding=encoding, errors=errors)
    try:
        yield wrapper
    finally:
        wrapper.detach()


class Parser(ABC):
    """
    Base class for implementing custom scening parsers.

    Parsers are responsible for reading file-like objects and converting them into `SceneRow` objects,
    which contain lists of frames or timestamps.

    To implement a parser, create a subclass and:

    1. Define the `filter` class variable.
    2. Implement the `parse` method.
    3. Register it using the `vsview_scening_register_parser` hook.
    """

    FileFilter: type[_FileFilter] = _FileFilter
    """Alias to `FileFilter` for convenience in subclasses."""

    filter: ClassVar[_FileFilter]
    """The file filter used by the plugin to identify supported files in the import dialog."""

    @abstractmethod
    def parse(self, io: BinaryIO, name: str, fps: Fraction) -> SceneRow | Sequence[SceneRow]:
        """
        Parse a binary stream into one or more SceneRow objects.

        Args:
            io: The input binary stream.
            name: The suggested name for the scene (usually the base filename).
            fps: The frame rate of the current video project, used for frame conversions.

        Returns:
            A single scene or a sequence of scenes.
        """
        ...

    if TYPE_CHECKING:

        def get_color(self) -> QColor:
            """
            Get a suggested color for the new scene row.

            This method is monkey-patched onto parser instances by the scening tool during the import process.
            It uses a golden-ratio-based generator to ensure distinct colors for adjacent scenes.

            Returns:
                A suggested color for the new scene.
            """
            ...


class Serializer(ABC):
    """
    Base class for implementing custom scening exporters.

    Serializers are responsible for taking a list of ranges and writing them to a
    file-like object in a specific format.

    To implement a serializer, create a subclass and:
    1. Define the `filter` class variable.
    2. Implement the `serialize` method.
    3. Register it using the `vsview_scening_register_serializer` hook.
    """

    FileFilter: type[_FileFilter] = _FileFilter
    """Alias to `FileFilter` for convenience in subclasses."""

    filter: ClassVar[_FileFilter]
    """The file filter used by the plugin to identify supported files in the export dialog."""

    @abstractmethod
    def serialize(self, io: BinaryIO, ranges: Iterator[UnifiedRange]) -> None:
        """
        Serialize a set of ranges into a binary stream.

        Args:
            io: The output binary stream.
            ranges: An iterator of UnifiedRange objects.
                These provide helpers like `as_frames()` and `as_times()` to facilitate format conversion.
        """
