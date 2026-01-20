"""
Category matching system for frame properties.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from jetpytools import Singleton, flatten, inject_self


@dataclass(slots=True)
class CategoryMatcher:
    """Defines how to match properties to a category."""

    name: str
    """Display name of the category."""

    priority: int = 0
    """Priority for matching. Higher priority is checked first."""

    order: int = 0
    """Display order in the tree view. Higher values appear first."""

    exact_matches: set[str] = field(default_factory=set)
    """Exact property keys that belong to this category."""

    prefixes: set[str] = field(default_factory=set)
    """Property key prefixes that belong to this category."""

    suffixes: set[str] = field(default_factory=set)
    """Property key suffixes that belong to this category."""

    def matches(self, prop_key: str) -> bool:
        """Check if a property key matches this category."""
        if prop_key in self.exact_matches:
            return True

        for prefix in self.prefixes:
            if prop_key.startswith(prefix):
                return True

        return any(prop_key.endswith(suffix) for suffix in self.suffixes)


type IterCategoryMatcher = Iterable[CategoryMatcher | IterCategoryMatcher]


class CategoryRegistry(Singleton):
    """Registry for property categories with extensible matching."""

    def __init__(self) -> None:
        self._matchers = list[CategoryMatcher]()

    @inject_self.property
    def default_category(self) -> str:
        return "Other"

    @inject_self
    def register(self, *matcher: CategoryMatcher | IterCategoryMatcher) -> None:
        self._matchers.extend(flatten(matcher))
        # Keep matchers sorted by priority (highest first)
        self._matchers.sort(key=lambda m: m.priority, reverse=True)

    @inject_self
    def get_category(self, prop_key: str) -> str:
        for matcher in self._matchers:
            if matcher.matches(prop_key):
                return matcher.name

        return self.default_category

    @inject_self
    def get_category_order(self, category_name: str) -> int:
        for matcher in self._matchers:
            if matcher.name == category_name:
                return matcher.order
        return -1  # Unknown categories go last


CategoryRegistry()
