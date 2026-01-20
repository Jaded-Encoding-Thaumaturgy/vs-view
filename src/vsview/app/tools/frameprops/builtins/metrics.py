"""Built-in metrics-related property categories and formatters."""

from __future__ import annotations

from ..categories import CategoryMatcher
from ..formatters import FormatterProperty

__all__ = ["METRICS_CATEGORY", "METRICS_FORMATTERS"]


# Category matcher for metrics properties
METRICS_CATEGORY = CategoryMatcher(
    name="Metrics",
    priority=8,
    order=80,
    exact_matches={
        # Scene detection
        "_SceneChangeNext",
        "_SceneChangePrev",
    },
    prefixes={
        # PlaneStats
        "PlaneStats",
    },
)


# Metrics property formatters
METRICS_FORMATTERS = [
    # Scene change detection
    FormatterProperty(
        prop_key="_SceneChangeNext",
        display_name="Scene Cut",
        value_formatter={0: "Current Scene", 1: "End of Scene"},
    ),
    FormatterProperty(
        prop_key="_SceneChangePrev",
        display_name="Scene Change",
        value_formatter={0: "Current Scene", 1: "Start of Scene"},
    ),
]
