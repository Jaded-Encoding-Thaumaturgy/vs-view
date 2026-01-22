import math
from typing import Any

from vstools import ChromaLocation, ColorRange, Field, FieldBased, Matrix, Primaries, Transfer

from vsview.app.tools.frameprops.api import CategoryMatcher, FormatterProperty, hookimpl


def _handle_na(value: int) -> str:
    return "N/A" if value < 0 else str(value)


def _format_ivtc_mics(mics: list[int]) -> str:
    return " | ".join(f"{key}: {_handle_na(value)}" for key, value in zip("pcnbu", mics))


def _handle_nan(value: Any) -> str:
    return "NaN" if isinstance(value, float) and math.isnan(value) else str(value)


def _format_wobbly_match(x: Any) -> str:
    if (x_s := str(x)) in ["p", "c", "n", "b", "u"]:
        return x_s

    if isinstance(x, int) and 0 <= x < 5:
        return ["p", "c", "n", "b", "u"][x]

    return "?"


def _format_orphan_frame(m: Any) -> str:
    return f"Yes ({m})" if not isinstance(m, int) or m >= 0 else "No"


def _format_preset_frames(r: Any) -> str:
    return str(r) if isinstance(r, int) else f"({r[0]}, {r[-1]})"


VIDEO_CATEGORY = CategoryMatcher(
    name="Video",
    priority=150 + 1,
    order=10,
    exact_matches={
        "_ChromaLocation",
        "_ColorRange",
        "_Matrix",
        "_Transfer",
        "_Primaries",
        "_FieldBased",
    },
)

VIDEO_FORMATTERS: list[FormatterProperty] = [
    FormatterProperty(
        prop_key="_ChromaLocation",
        value_formatter=lambda v: ChromaLocation.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_ColorRange",
        value_formatter=lambda v: ColorRange.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_Matrix",
        value_formatter=lambda v: Matrix.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_Transfer",
        value_formatter=lambda v: Transfer.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_Primaries",
        value_formatter=lambda v: Primaries.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_FieldBased",
        value_formatter=lambda v: FieldBased.from_param(v).pretty_string,
    ),
    FormatterProperty(
        prop_key="_Field",
        value_formatter=lambda v: Field.from_param(v).pretty_string,
    ),
]


FIELD_CATEGORY = CategoryMatcher(
    name="Field",
    priority=50 + 1,
    order=30,
    prefixes={
        # VIVTC
        "VFM",
        "VDecimate",
        # TIVTC
        "TFM",
        "TDecimate",
    },
)


FIELD_FORMATTERS = [
    # VIVTC
    FormatterProperty(
        prop_key="VFMMics",
        value_formatter=_format_ivtc_mics,
    ),
    FormatterProperty(
        prop_key="VFMMatch",
        value_formatter={0: "p", 1: "c", 2: "n", 3: "b", 4: "u"},
    ),
    FormatterProperty(
        prop_key="VFMSceneChange",
        value_formatter={0: "No", 1: "Yes"},
    ),
    FormatterProperty(
        prop_key="VDecimateDrop",
        value_formatter={0: "No", 1: "Yes"},
    ),
    FormatterProperty(
        prop_key="VDecimateMaxBlockDiff",
        value_formatter=str,
    ),
    FormatterProperty(
        prop_key="VDecimateTotalDiff",
        value_formatter=str,
    ),
    # TIVTC
    FormatterProperty(
        prop_key="TFMMics",
        value_formatter=_format_ivtc_mics,
    ),
    FormatterProperty(
        prop_key="TFMMatch",
        value_formatter={0: "p", 1: "c", 2: "n", 3: "b", 4: "u"},
    ),
]


METRICS_CATEGORY = CategoryMatcher(
    name="Metrics",
    priority=100 + 1,
    order=20,
    exact_matches={
        "SceneChange",
        # DMetrics
        "MMetrics",
        "VMetrics",
        "SSIMULACRA2",
    },
    prefixes={
        "psm",
        "VFMPlaneStats",
        # DGIndex (via vssource)
        "Dgi",
        # Scene-based graining (via lvsfunc)
        "SceneGrain",
        # Packet sizes (via lvsfunc)
        "Pkt",
        # VMAF
        "ciede2000",
        "psnr_",
    },
    suffixes={
        # VMAF
        "_ssim",
    },
)


METRICS_FORMATTERS = [
    # DMetrics
    FormatterProperty(prop_key="MMetrics"),
    FormatterProperty(prop_key="VMetrics"),
    # Packet sizes
    FormatterProperty(prop_key="PktSize", value_formatter=_handle_na),
    FormatterProperty(prop_key="PktSceneAvgSize", value_formatter=_handle_na),
    FormatterProperty(prop_key="PktSceneMinSize", value_formatter=_handle_na),
    FormatterProperty(prop_key="PktSceneMaxSize", value_formatter=_handle_na),
    # VMAF
    FormatterProperty(prop_key="ciede2000", value_formatter=_handle_nan),
    FormatterProperty(prop_key="float_ssim", value_formatter=_handle_nan),
    FormatterProperty(prop_key="float_ms_ssim", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_y", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_cb", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_cr", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_hvs", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_hvs_y", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_hvs_cb", value_formatter=_handle_nan),
    FormatterProperty(prop_key="psnr_hvs_cr", value_formatter=_handle_nan),
    # DGIndex
    FormatterProperty(prop_key="DgiFieldOp"),
    FormatterProperty(prop_key="DgiOrder"),
    FormatterProperty(prop_key="DgiFilm", value_formatter=lambda v: f"{v}%"),
]


WOBBLY_CATEGORY = CategoryMatcher(name="Wobbly", order=100, prefixes={"Wobbly"})


WOBBLY_FORMATTERS = [
    FormatterProperty(prop_key="WobblyMatch", value_formatter=_format_wobbly_match),
    FormatterProperty(prop_key="WobblyCombed", value_formatter={0: "No", 1: "Yes"}),
    FormatterProperty(prop_key="WobblyCycleFps"),
    FormatterProperty(prop_key="WobblyFreeze"),
    FormatterProperty(prop_key="WobblyInterlacedFades", value_formatter={0: "No", 1: "Yes"}),
    FormatterProperty(prop_key="WobblyOrphanFrame", value_formatter=_format_orphan_frame),
    FormatterProperty(prop_key="WobblyOrphanDeinterlace", value_formatter=_format_orphan_frame),
    FormatterProperty(prop_key="WobblyPreset"),
    FormatterProperty(prop_key="WobblyPresetPosition", value_formatter=lambda v: str(v).title()),
    FormatterProperty(prop_key="WobblyPresetFrames", value_formatter=_format_preset_frames),
]


@hookimpl
def vsview_frameprops_register_category_matchers() -> Any:
    return [VIDEO_CATEGORY, FIELD_CATEGORY, METRICS_CATEGORY, WOBBLY_CATEGORY]


@hookimpl
def vsview_frameprops_register_formatter_properties() -> Any:
    return VIDEO_FORMATTERS, FIELD_FORMATTERS, METRICS_FORMATTERS, WOBBLY_FORMATTERS
