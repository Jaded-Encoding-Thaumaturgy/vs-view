from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from importlib.metadata import EntryPoint
from typing import Any

import pluggy
import pytest

from vsview.app.plugins import specs
from vsview.app.plugins.entrypoints import collect_hook_results, load_entrypoints

pytestmark = [pytest.mark.unit]

type SetEntryPoints = Callable[[str, Sequence[EntryPoint]], None]
type MakeEntryPoint = Callable[[str, object], EntryPoint]

GROUP = "vsview.audit_test"
MISSING_MODULE_VALUE = "vsview_audit_missing_module:plugin"


class _PlainPlugin:
    pass


class _InvalidHookPlugin:
    @specs.hookimpl
    def vsview_register_tooldock(self, unexpected_arg: int) -> None: ...


@pytest.fixture
def make_ep(entry_point_target: Callable[[object], str]) -> MakeEntryPoint:
    def make(name: str, target: object) -> EntryPoint:
        value = target if isinstance(target, str) else entry_point_target(target)
        return EntryPoint(name=name, value=value, group=GROUP)

    return make


@pytest.fixture
def manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager("vsview")
    pm.add_hookspecs(specs)
    return pm


def test_load_entrypoints_isolates_each_entry_point(
    manager: pluggy.PluginManager,
    fake_entry_points: SetEntryPoints,
    make_ep: MakeEntryPoint,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plugin_a, plugin_b = _PlainPlugin(), _PlainPlugin()
    invalid_hook_ep = make_ep("plugin_invalid_hook", _InvalidHookPlugin())
    fake_entry_points(
        GROUP,
        [
            make_ep("plugin_a", plugin_a),
            make_ep("plugin_missing_dep", MISSING_MODULE_VALUE),
            invalid_hook_ep,
            make_ep("plugin_b", plugin_b),
        ],
    )

    with caplog.at_level(logging.ERROR, logger="vsview.app.plugins.entrypoints"):
        count = load_entrypoints(manager, GROUP)

    assert count == 2
    assert manager.get_plugin("plugin_a") is plugin_a
    assert manager.get_plugin("plugin_b") is plugin_b
    assert manager.has_plugin("plugin_missing_dep") is False
    assert manager.has_plugin("plugin_invalid_hook") is False
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        f"Couldn't load entry point 'plugin_missing_dep' ({MISSING_MODULE_VALUE}) of group '{GROUP}'",
        f"Couldn't load entry point 'plugin_invalid_hook' ({invalid_hook_ep.value}) of group '{GROUP}'",
    ]
    assert all(r.exc_info is not None for r in caplog.records)


def test_load_entrypoints_is_safe_to_call_again(
    manager: pluggy.PluginManager, fake_entry_points: SetEntryPoints, make_ep: MakeEntryPoint
) -> None:
    plugin_a = _PlainPlugin()
    fake_entry_points(GROUP, [make_ep("plugin_a", plugin_a), make_ep("plugin_missing_dep", MISSING_MODULE_VALUE)])

    first = load_entrypoints(manager, GROUP)
    second = load_entrypoints(manager, GROUP)

    assert (first, second) == (1, 0)
    assert manager.get_plugin("plugin_a") is plugin_a


def test_load_entrypoints_skips_blocked_names(
    manager: pluggy.PluginManager, fake_entry_points: SetEntryPoints, make_ep: MakeEntryPoint
) -> None:
    plugin_a, plugin_b = _PlainPlugin(), _PlainPlugin()
    fake_entry_points(GROUP, [make_ep("plugin_a", plugin_a), make_ep("plugin_b", plugin_b)])
    manager.set_blocked("plugin_a")

    count = load_entrypoints(manager, GROUP)

    assert count == 1
    assert manager.get_plugin("plugin_b") is plugin_b
    assert manager.is_registered(plugin_a) is False


_test_hookspec = pluggy.HookspecMarker("vsview_audit_test")
_test_hookimpl = pluggy.HookimplMarker("vsview_audit_test")


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
    pm = pluggy.PluginManager("vsview_audit_test")
    pm.add_hookspecs(_Specs)
    return pm


def test_collect_hook_results_isolates_each_implementation(
    hook_manager: pluggy.PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    single, nested = _Item("single"), [_Item("nested_1"), [_Item("nested_2")]]
    hook_manager.register(_Provider(single), name="single")
    hook_manager.register(_RaisingProvider(), name="raising")
    hook_manager.register(_Provider(nested), name="nested")
    hook_manager.register(_Provider(None), name="none")

    with caplog.at_level(logging.WARNING, logger="vsview.app.plugins.entrypoints"):
        results = collect_hook_results(hook_manager.hook.provide, _Item)

    assert [r.label for r in results] == ["nested_1", "nested_2", "single"]
    assert [r.getMessage() for r in caplog.records] == ["Hook 'provide' of plugin 'raising' failed"]
    assert caplog.records[0].exc_info is not None


def test_collect_hook_results_skips_values_of_the_wrong_type(
    hook_manager: pluggy.PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    hook_manager.register(_Provider([_Item("good"), _Item, "text"]), name="mixed")

    with caplog.at_level(logging.WARNING, logger="vsview.app.plugins.entrypoints"):
        results = collect_hook_results(hook_manager.hook.provide, _Item)

    assert [r.label for r in results] == ["good"]
    assert [r.getMessage() for r in caplog.records] == [
        f"Hook 'provide' of plugin 'mixed' returned {_Item!r}, which is not a _Item. It will be ignored.",
        "Hook 'provide' of plugin 'mixed' returned 'text', which is not a _Item. It will be ignored.",
    ]


def test_collect_hook_results_keeps_pluggy_call_order(hook_manager: pluggy.PluginManager) -> None:
    items = [_Item(str(i)) for i in range(3)]
    for i, item in enumerate(items):
        hook_manager.register(_Provider(item), name=f"provider_{i}")
    hook_manager.register(_Wrapper(), name="wrapper")

    results = collect_hook_results(hook_manager.hook.provide, _Item)

    assert results == hook_manager.hook.provide()
