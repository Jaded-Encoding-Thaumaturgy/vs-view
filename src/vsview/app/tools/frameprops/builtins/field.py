"""Built-in field/interlacing-related property categories and formatters."""

from __future__ import annotations

from ..categories import CategoryMatcher
from ..formatters import FormatterProperty

# Category matcher for field properties
FIELD_CATEGORY = CategoryMatcher(
    name="Field",
    priority=7,
    order=70,
    exact_matches={
        "_Combed",
        "_Field",
    },
)


# Field property formatters
FIELD_FORMATTERS = [
    # Basic field properties
    FormatterProperty(
        prop_key="_Combed",
        display_name="Is Combed",
        value_formatter={0: "No", 1: "Yes"},
    ),
    FormatterProperty(
        prop_key="_Field",
        display_name="Frame Field Type",
        value_formatter={0: "Bottom Field", 1: "Top Field"},
    ),
]
