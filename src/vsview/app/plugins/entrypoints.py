from __future__ import annotations

import importlib.metadata
from logging import getLogger

import pluggy
from jetpytools import flatten

logger = getLogger(__name__)


def load_entrypoints(manager: pluggy.PluginManager, group: str) -> int:
    """
    Load and register all entry points of `group`, logging and skipping any that fail.

    Already registered or blocked names are ignored, making retries idempotent.

    Returns:
        The number of newly registered entry points.
    """
    count = 0

    for ep in importlib.metadata.entry_points(group=group):
        if manager.has_plugin(ep.name) or manager.is_blocked(ep.name):
            continue

        try:
            manager.register(ep.load(), name=ep.name)
        except Exception:
            logger.exception("Couldn't load entry point %r (%s) of group %r", ep.name, ep.value, group)
            continue

        count += 1

    return count


def collect_hook_results[T](hook: pluggy.HookCaller, expected: type[T] | tuple[type[T], ...]) -> list[T]:
    """
    Call each non-wrapper hook implementation in pluggy call order and collect `expected` results.

    Return values are flattened; failing implementations and mismatched types are logged and skipped.
    """
    expected_types = expected if isinstance(expected, tuple) else (expected,)
    expected_names = " or ".join(t.__name__ for t in expected_types)
    results = list[T]()

    for impl in reversed(hook.get_hookimpls()):
        if impl.wrapper or impl.hookwrapper:
            continue

        try:
            values = list(flatten([impl.function()]))
        except Exception:
            logger.exception("Hook %r of plugin %r failed", hook.name, impl.plugin_name)
            continue

        for value in values:
            if value is None:
                continue

            if isinstance(value, expected_types):
                results.append(value)
            else:
                logger.warning(
                    "Hook %r of plugin %r returned %r, which is not a %s. It will be ignored.",
                    hook.name,
                    impl.plugin_name,
                    value,
                    expected_names,
                )

    return results
