from collections.abc import Iterable
from typing import BinaryIO, override

from .api import Serializer, borrowed_text_wrapper
from .models import UnifiedRange


class OGMSerializer(Serializer):
    filter = Serializer.FileFilter("OGM Chapters", "txt")

    @override
    def serialize(self, io: BinaryIO, ranges: Iterable[UnifiedRange]) -> None:
        with borrowed_text_wrapper(io) as wrapper:
            for i, r in enumerate(ranges, 1):
                timestamp = r.as_times()[0].to_ts()
                label = r.label or f"Chapter {i:02}"

                wrapper.write(f"CHAPTER{i:02}={timestamp}\n")
                wrapper.write(f"CHAPTER{i:02}NAME={label}\n")


class QPFileSerializer(Serializer):
    filter = Serializer.FileFilter("QP File", "qp")

    @override
    def serialize(self, io: BinaryIO, ranges: Iterable[UnifiedRange]) -> None:
        with borrowed_text_wrapper(io) as wrapper:
            for r in ranges:
                wrapper.write(f"{r.as_frames()[0]} K" + (f" {r.label}" if r.label else "") + "\n")


class PythonListFramesSerializer(Serializer):
    filter = Serializer.FileFilter("Python List (Frames)", "txt")

    @override
    def serialize(self, io: BinaryIO, ranges: Iterable[UnifiedRange]) -> None:
        with borrowed_text_wrapper(io) as wrapper:
            wrapper.write(str([r.as_frames() for r in ranges]))


class PythonListTimestampsSerializer(Serializer):
    filter = Serializer.FileFilter("Python List (Timestamps)", "txt")

    @override
    def serialize(self, io: BinaryIO, ranges: Iterable[UnifiedRange]) -> None:
        with borrowed_text_wrapper(io) as wrapper:
            wrapper.write(
                str([tuple(ts.to_ts("{H:02d}:{M:02d}:{S:02d}.{ms:03d}") for ts in r.as_times()) for r in ranges])
            )


internal_serializers: list[Serializer] = [
    OGMSerializer(),
    QPFileSerializer(),
    PythonListFramesSerializer(),
    PythonListTimestampsSerializer(),
]
