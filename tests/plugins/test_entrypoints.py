from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import pluggy
import pytest

from vsview.app.plugins import specs
from vsview.app.plugins.entrypoints import collect_hook_results, load_entrypoints

pytestmark = [pytest.mark.unit]

type SetEntryPoints = Callable[[str, Mapping[str, Any]], None]

GROUP = "vsview.test_entrypoints"
ENTRYPOINT_LOGGER = "vsview.app.plugins.entrypoints"


class _PlainPlugin: ...


class _InvalidHookPlugin:
    @specs.hookimpl
    def vsview_register_tooldock(self, unexpected_arg: int) -> None: ...


@pytest.fixture
def manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager("vsview")
    pm.add_hookspecs(specs)
    return pm


def test_load_entrypoints_isolates_failures_and_skips_blocked(
    manager: pluggy.PluginManager, fake_entry_points: SetEntryPoints, caplog: pytest.LogCaptureFixture
) -> None:
    plugin_a, plugin_b = _PlainPlugin(), _PlainPlugin()
    fake_entry_points(
        GROUP,
        {
            "blocked_plugin": _PlainPlugin(),
            "plugin_a": plugin_a,
            "plugin_missing_dep": "vsview_missing_module:plugin",
            "plugin_invalid_hook": _InvalidHookPlugin(),
            "plugin_b": plugin_b,
        },
    )
    manager.set_blocked("blocked_plugin")

    with caplog.at_level(logging.ERROR, logger=ENTRYPOINT_LOGGER):
        count = load_entrypoints(manager, GROUP)

    assert count == 2
    assert manager.get_plugin("plugin_a") is plugin_a
    assert manager.get_plugin("plugin_b") is plugin_b
    assert manager.has_plugin("blocked_plugin") is False
    assert manager.has_plugin("plugin_missing_dep") is False
    assert manager.has_plugin("plugin_invalid_hook") is False
    assert [r.getMessage() for r in caplog.records] == [
        f"Couldn't load entry point 'plugin_missing_dep' (vsview_missing_module:plugin) of group '{GROUP}'",
        f"Couldn't load entry point 'plugin_invalid_hook' (test:plugin_invalid_hook) of group '{GROUP}'",
    ]
    assert all(r.exc_info is not None for r in caplog.records)


def test_load_entrypoints_is_idempotent(manager: pluggy.PluginManager, fake_entry_points: SetEntryPoints) -> None:
    plugin_a = _PlainPlugin()
    fake_entry_points(GROUP, {"plugin_a": plugin_a, "plugin_missing_dep": "vsview_missing_module:plugin"})

    assert (load_entrypoints(manager, GROUP), load_entrypoints(manager, GROUP)) == (1, 0)
    assert manager.get_plugin("plugin_a") is plugin_a


_test_hookspec = pluggy.HookspecMarker("vsview_entrypoints_test")
_test_hookimpl = pluggy.HookimplMarker("vsview_entrypoints_test")


class _Specs:
    @_test_hookspec
    def provide(self) -> Any: ...


class _Item:
    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:
        return f"_Item({self.label!r})"


class _Provider:
    def __init__(self, result: Any) -> None:
        self.result = result

    @_test_hookimpl
    def provide(self) -> Any:
        return self.result


class _RaisingProvider:
    @_test_hookimpl
    def provide(self) -> Any:
        raise RuntimeError("broken extension")


class _Wrapper:
    @_test_hookimpl(wrapper=True)
    def provide(self) -> Any:
        return (yield)


@pytest.fixture
def hook_manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager("vsview_entrypoints_test")
    pm.add_hookspecs(_Specs)
    return pm


def test_collect_hook_results_isolates_failures_and_preserves_order(
    hook_manager: pluggy.PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    hook_manager.register(_Provider(_Item("first")), name="first")
    hook_manager.register(_RaisingProvider(), name="raising")
    hook_manager.register(_Provider([_Item("second"), [_Item("third")]]), name="nested")
    hook_manager.register(_Provider(None), name="none")
    hook_manager.register(_Wrapper(), name="wrapper")

    with caplog.at_level(logging.ERROR, logger=ENTRYPOINT_LOGGER):
        results = collect_hook_results(hook_manager.hook.provide, _Item)

    assert [r.label for r in results] == ["second", "third", "first"]
    assert [r.getMessage() for r in caplog.records] == ["Hook 'provide' of plugin 'raising' failed"]
    assert caplog.records[0].exc_info is not None


def test_collect_hook_results_ignores_wrong_types(
    hook_manager: pluggy.PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    hook_manager.register(_Provider([_Item("good"), _Item, "text"]), name="mixed")

    with caplog.at_level(logging.WARNING, logger=ENTRYPOINT_LOGGER):
        results = collect_hook_results(hook_manager.hook.provide, _Item)

    assert [r.label for r in results] == ["good"]
    assert [r.getMessage() for r in caplog.records] == [
        f"Hook 'provide' of plugin 'mixed' returned {_Item!r}, which is not a _Item. It will be ignored.",
        "Hook 'provide' of plugin 'mixed' returned 'text', which is not a _Item. It will be ignored.",
    ]
