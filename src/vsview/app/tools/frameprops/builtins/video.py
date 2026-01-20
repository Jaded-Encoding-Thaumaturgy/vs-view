"""Built-in video-related property categories and formatters."""

from __future__ import annotations

from enum import Enum

from vapoursynth import (
    ChromaLocation,
    ColorPrimaries,
    ColorRange,
    FieldBased,
    MatrixCoefficients,
    TransferCharacteristics,
)

from ..categories import CategoryMatcher
from ..formatters import CompoundFormatterProperty, FormatterProperty

# Category matcher for video properties
VIDEO_CATEGORY = CategoryMatcher(
    name="Video",
    priority=10,
    order=100,
    exact_matches={
        # Colorimetry
        "_ChromaLocation",
        "_ColorRange",
        "_Matrix",
        "_Transfer",
        "_Primaries",
        # Frame Properties
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


def _format_duration(num: int | None, den: int | None) -> str:
    if num is None or den is None:
        return "N/A"
    if den == 0:
        return "Invalid (den=0)"
    return f"{num / den:.6g}s ({num}/{den})"


def _format_sar(num: int | None, den: int | None) -> str:
    if num is None or den is None:
        return "N/A"
    if den == 0:
        return "Invalid (den=0)"
    return f"{num}:{den}"


# Video property formatters
VIDEO_FORMATTERS: list[FormatterProperty | CompoundFormatterProperty] = [
    # Enum-based formatters
    FormatterProperty(
        prop_key="_ChromaLocation",
        display_name="Chroma Location",
        value_formatter=lambda v: _format_enum(v, ChromaLocation).title(),
    ),
    FormatterProperty(
        prop_key="_ColorRange",
        display_name="Color Range",
        value_formatter=lambda v: _format_enum(v, ColorRange).title(),
    ),
    FormatterProperty(
        prop_key="_Matrix",
        display_name="Matrix",
        value_formatter=lambda v: _format_enum(v, MatrixCoefficients),
    ),
    FormatterProperty(
        prop_key="_Transfer",
        display_name="Transfer",
        value_formatter=lambda v: _format_enum(v, TransferCharacteristics),
    ),
    FormatterProperty(
        prop_key="_Primaries",
        display_name="Primaries",
        value_formatter=lambda v: _format_enum(v, ColorPrimaries),
    ),
    FormatterProperty(
        prop_key="_FieldBased",
        display_name="Field Type",
        value_formatter=lambda v: _format_enum(v, FieldBased).title(),
    ),
    FormatterProperty(
        prop_key="_PictType",
        display_name="Picture Type",
    ),
    # Compound formatters (merged properties)
    CompoundFormatterProperty(
        prop_keys=("_DurationNum", "_DurationDen"),
        display_name="Duration",
        value_formatter=_format_duration,
    ),
    CompoundFormatterProperty(
        prop_keys=("_SARNum", "_SARDen"),
        display_name="SAR",
        value_formatter=_format_sar,
    ),
]
