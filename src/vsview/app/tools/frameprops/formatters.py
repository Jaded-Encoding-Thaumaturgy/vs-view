"""
Property formatter system for frame properties.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from logging import getLogger
from typing import Any, cast

from jetpytools import Singleton, flatten, inject_self
from vapoursynth import VideoFrame

__all__ = ["CompoundFormatterProperty", "FormatterProperty", "FormatterRegistry"]

logger = getLogger(__name__)


@dataclass(slots=True)
class FormatterProperty:
    """Defines how to format a property for display."""

    prop_key: str
    """The property key this formatter applies to."""

    display_name: str | Callable[[str], str]
    """Static display name or a callable that generates it from the key."""

    value_formatter: Callable[[Any], str] | dict[Hashable, str] | None = None
    """
    Optional value formatter:
    - Callable: Transform the value to a string
    - dict: Map values to strings (for enums/lookups)
    - None: Use default str() conversion
    """

    def format_key(self, key: str) -> str:
        if callable(self.display_name):
            return self.display_name(key)
        return self.display_name

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


@dataclass(slots=True)
class CompoundFormatterProperty:
    """Defines how to format multiple properties into a single display row."""

    prop_keys: tuple[str, ...]
    """The property keys this formatter combines."""

    display_name: str
    """Display name for the combined property."""

    value_formatter: Callable[..., str]
    """
    Formatter that takes multiple values (in the same order as prop_keys)
    and returns a formatted string. Values will be None if the property is not present.
    """

    def format_value(self, props: Mapping[str, Any]) -> str:
        values = [props.get(key) for key in self.prop_keys]
        try:
            return self.value_formatter(*values)
        except Exception:
            logger.exception("There was an error when trying to format compound %r:", self.prop_keys)
            return " / ".join(FormatterProperty.default_format(v) for v in values if v is not None)


type IterFormatter = Iterable[FormatterProperty | CompoundFormatterProperty | IterFormatter]


class FormatterRegistry(Singleton):
    """Registry for property formatters."""

    def __init__(self) -> None:
        self._formatters = dict[str, FormatterProperty]()
        self._compound_formatters = list[CompoundFormatterProperty]()
        self._consumed_keys = set[str]()  # Keys that are consumed by compound formatters
        self._order = dict[str, int]()  # Track registration order
        self._next_order = 0

    @inject_self.property
    def compound_formatters(self) -> list[CompoundFormatterProperty]:
        """Get all registered compound formatters."""
        return list(self._compound_formatters)

    @inject_self
    def register(self, *formatter: FormatterProperty | CompoundFormatterProperty | IterFormatter) -> None:
        """Register a property formatter or compound formatter."""
        formatters = cast(Iterable[FormatterProperty | CompoundFormatterProperty], flatten(formatter))

        for f in formatters:
            if isinstance(f, CompoundFormatterProperty):
                self._compound_formatters.append(f)
                self._consumed_keys.update(f.prop_keys)

                # Use the first key for ordering purposes
                if f.prop_keys[0] not in self._order:
                    self._order[f.prop_keys[0]] = self._next_order
                    self._next_order -= 1
            else:
                self._formatters[f.prop_key] = f

                if f.prop_key not in self._order:
                    self._order[f.prop_key] = self._next_order
                    self._next_order -= 1

    @inject_self
    def get_property_order(self, prop_key: str) -> int:
        """Get the display order for a property. Returns low value for unregistered properties."""
        return self._order.get(prop_key, -1000)

    @inject_self
    def is_consumed_by_compound(self, prop_key: str) -> bool:
        """Check if a property key is consumed by a compound formatter."""
        return prop_key in self._consumed_keys

    @inject_self
    def format_property(self, key: str, value: Any) -> tuple[str, str]:
        """Format a property key and value for display.

        Args:
            key: The property key.
            value: The property value.

        Returns:
            A tuple of (formatted_key, formatted_value).
        """
        if key in self._formatters:
            formatter = self._formatters[key]
            return formatter.format_key(key), formatter.format_value(value)

        # Default formatting
        display_key = key.lstrip("_")
        display_value = FormatterProperty.default_format(value)
        return display_key, display_value


FormatterRegistry()
