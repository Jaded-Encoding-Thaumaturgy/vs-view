from __future__ import annotations

from enum import Enum

from vapoursynth import (
    ChromaLocation,
    ColorPrimaries,
    FieldBased,
    MatrixCoefficients,
    Range,
    TransferCharacteristics,
)

from ..categories import CategoryMatcher
from ..formatters import FormatterProperty

VIDEO_CATEGORY = CategoryMatcher(
    name="Video",
    priority=150,
    order=10,
    exact_matches={
        # Colorimetry
        "_ChromaLocation",
        "_Range",
        "_Matrix",
        "_Transfer",
        "_Primaries",
        # Others
        "_Alpha",
        "_PictType",
        "_DurationNum",
        "_DurationDen",
        "_AbsoluteTime",
        "_SARNum",
        "_SARDen",
        "_FieldBased",
    },
)


def _format_enum(value: int, enum: type[Enum]) -> str:
    return enum(value).name.split("_", 1)[1:][0]


VIDEO_FORMATTERS: list[FormatterProperty] = [
    # Enum-based formatters
    FormatterProperty(
        prop_key="_ChromaLocation",
        value_formatter=lambda v: _format_enum(v, ChromaLocation).title(),
    ),
    FormatterProperty(
        prop_key="_Range",
        value_formatter=lambda v: _format_enum(v, Range).title(),
    ),
    FormatterProperty(
        prop_key="_Matrix",
        value_formatter=lambda v: _format_enum(v, MatrixCoefficients),
    ),
    FormatterProperty(
        prop_key="_Transfer",
        value_formatter=lambda v: _format_enum(v, TransferCharacteristics),
    ),
    FormatterProperty(
        prop_key="_Primaries",
        value_formatter=lambda v: _format_enum(v, ColorPrimaries),
    ),
    FormatterProperty(
        prop_key="_FieldBased",
        value_formatter=lambda v: _format_enum(v, FieldBased).title(),
    ),
    # Others
    FormatterProperty(prop_key="_PictType"),
    FormatterProperty(prop_key="_DurationNum"),
    FormatterProperty(prop_key="_DurationDen"),
    FormatterProperty(prop_key="_SARNum"),
    FormatterProperty(prop_key="_SARDen"),
]
