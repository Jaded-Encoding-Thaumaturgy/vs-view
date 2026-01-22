"""
Property formatter system for frame properties.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from logging import getLogger
from typing import Any

from jetpytools import Singleton, flatten, inject_self
from vapoursynth import VideoFrame

__all__ = ["FormatterProperty", "FormatterRegistry"]

logger = getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class FormatterProperty:
    """Defines how to format a property for display."""

    prop_key: str
    """The property key this formatter applies to."""

    value_formatter: Callable[[Any], str] | dict[Hashable, str] | None = None
    """
    Optional value formatter:
    - Callable: Transform the value to a string
    - dict: Map values to strings (for enums/lookups)
    - None: Use default str() conversion
    """

    def format_value(self, value: Any) -> str:
        if callable(self.value_formatter):
            try:
                return self.value_formatter(value)
            except Exception:
                logger.exception("There was an error when trying to format %r:", self.prop_key)
                return self.default_format(value)

        # Dictionary lookup (for enums)
        if isinstance(self.value_formatter, dict):
            return self.value_formatter.get(value, self.default_format(value))

        return self.default_format(value)

    @staticmethod
    def default_format(value: Any, repr_frame: bool = False) -> str:
        match value:
            case bytes():
                return value.decode("utf-8")
            case float():
                return f"{value:.6g}"
            case VideoFrame():
                return repr(value) if repr_frame else str(value).replace("\t", "    ").rstrip()
            case _:
                return str(value)


type IterFormatter = Iterable[FormatterProperty | IterFormatter]


class FormatterRegistry(Singleton):
    """Registry for property formatters."""

    def __init__(self) -> None:
        self._formatters = dict[str, FormatterProperty]()
        self._order = dict[str, int]()  # Track registration order
        self._next_order = 0

    @inject_self
    def has_formatter(self, key: str) -> bool:
        """Check if a key has a configurable formatter."""

        return key in self._formatters and self._formatters[key].value_formatter is not None

    @inject_self
    def register(self, *formatter: FormatterProperty | IterFormatter) -> None:
        """Register a property formatter."""

        for f in flatten(formatter):
            self._formatters[f.prop_key] = f

            if f.prop_key not in self._order:
                self._order[f.prop_key] = self._next_order
                self._next_order -= 1

    @inject_self
    def get_property_order(self, prop_key: str) -> int:
        """Get the display order for a property. Returns low value for unregistered properties."""
        return self._order.get(prop_key, -1000)

    @inject_self
    def format_value(self, key: str, value: Any) -> str:
        """Format a property value for display."""
        if key in self._formatters:
            return self._formatters[key].format_value(value)

        return FormatterProperty.default_format(value)


FormatterRegistry()
