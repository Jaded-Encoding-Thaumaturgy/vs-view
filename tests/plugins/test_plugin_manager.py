from __future__ import annotations

import gc
import sys
import weakref
from collections.abc import Callable, Generator, Mapping, Sequence
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Annotated, Any

import pytest
import vapoursynth as vs
from pydantic import BaseModel
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.app import tools
from vsview.app.plugins import specs
from vsview.app.plugins.api import NodeProcessor, WidgetPluginBase
from vsview.app.plugins.manager import Notifier, PluginManager
from vsview.app.settings.action import ActionDefinition
from vsview.app.settings.dialog import SettingsDialog
from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.metadata import Checkbox
from vsview.app.settings.shortcuts import ShortcutManager
from vsview.app.tools import audio_convert as builtin_audio_convert
from vsview.app.tools.audio_convert.plugin import AudioConvert as BuiltinAudioConvert
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]

type LoadPlugins = Callable[[Mapping[str, Any]], PluginManager]

FIRST_PARTY_IDENTIFIER = "jet_vsview_colorpicker"


@pytest.fixture(autouse=True)
def isolate_plugin_manager_singleton() -> Generator[None]:
    type(PluginManager)._instances.pop(PluginManager, None)
    yield
    type(PluginManager)._instances.pop(PluginManager, None)


@pytest.fixture(autouse=True)
def isolate_shortcut_definitions() -> Generator[None]:
    orig = ShortcutManager.definitions.copy()
    try:
        yield
    finally:
        ShortcutManager.definitions.clear()
        ShortcutManager.definitions.update(orig)


@pytest.fixture
def extra_tools_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Add a folder of extra first-party tool modules to `vsview.app.tools`."""
    monkeypatch.setattr(tools, "__path__", [*tools.__path__, str(tmp_path)])
    try:
        yield tmp_path
    finally:
        for name in [n for n in sys.modules if n.startswith("vsview.app.tools.audit_")]:
            del sys.modules[name]


@pytest.fixture
def load_plugins(
    qt_event_loop: QtEventLoop, fake_entry_points: Callable[[str, Mapping[str, Any]], None]
) -> LoadPlugins:
    """Load the first-party tools plus the given `vsview` entry points through `PluginManager.load`."""

    def load(entries: Mapping[str, Any]) -> PluginManager:
        fake_entry_points("vsview", entries)
        pm = PluginManager()
        pm.load()
        pm.wait_for_loaded()
        return pm

    return load


def make_tool(
    identifier: str,
    *,
    global_model: type[BaseModel] | None = None,
    local_model: type[BaseModel] | None = None,
    shortcuts: Any = (),
) -> type:
    return type(
        "Tool",
        (),
        {
            "identifier": identifier,
            "display_name": identifier.replace("_", " ").title(),
            "global_settings_model": global_model,
            "local_settings_model": local_model,
            "shortcuts": shortcuts,
        },
    )


class ToolDock:
    """Entry point object that registers `tool` as a tool dock."""

    def __init__(self, tool: type) -> None:
        self.tool = tool

    @specs.hookimpl
    def vsview_register_tooldock(self) -> type:
        return self.tool


class RaisingToolDock:
    @specs.hookimpl
    def vsview_register_tooldock(self) -> type:
        raise RuntimeError("broken hook")


def hook_plugin(hook_name: str, result: object) -> object:
    """Return a plugin object implementing a single hook that returns `result`."""

    @specs.hookimpl
    def impl() -> object:
        return result

    return type("HookPlugin", (), {hook_name: staticmethod(impl)})()


class CallbackHolder:
    def __init__(self) -> None:
        self.calls = 0

    def on_notify(self) -> None:
        self.calls += 1


# Notifier
def test_notifier_calls_each_callback_once_however_often_it_is_notified(
    qt_event_loop: QtEventLoop, qtbot: QtBot
) -> None:
    notifier = Notifier()
    holder = CallbackHolder()
    function_calls = list[None]()
    notifier.register(holder.on_notify)
    notifier.register(lambda: function_calls.append(None))

    notifier.notify()
    notifier.notify()

    qtbot.waitUntil(lambda: holder.calls == 1 and len(function_calls) == 1, timeout=2000)
    qtbot.wait(50)
    assert (holder.calls, len(function_calls)) == (1, 1)


def test_notifier_calls_a_callback_registered_after_notify(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    holder = CallbackHolder()
    notifier.notify()

    notifier.register(holder.on_notify)

    qtbot.waitUntil(lambda: holder.calls == 1, timeout=2000)


def test_notifier_does_not_keep_bound_method_owners_alive(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    dead, survivor = CallbackHolder(), CallbackHolder()
    dead_ref = weakref.ref(dead)
    notifier.register(dead.on_notify)
    notifier.register(survivor.on_notify)

    del dead
    gc.collect()
    notifier.notify()

    assert dead_ref() is None
    qtbot.waitUntil(lambda: survivor.calls == 1, timeout=2000)


# Accessors
@pytest.mark.parametrize(
    ("hook_name", "accessor", "as_list"),
    [
        ("vsview_register_tooldock", "tooldocks", True),
        ("vsview_register_toolpanel", "toolpanels", True),
        ("vsview_get_video_processor", "video_processor", False),
        ("vsview_get_audio_processor", "audio_processor", False),
    ],
)
def test_accessor_returns_what_the_hook_registers(
    init_plugin_manager: PluginManager, hook_name: str, accessor: str, as_list: bool
) -> None:
    tool = make_tool("registered_tool")
    init_plugin_manager.manager.register(hook_plugin(hook_name, tool))

    assert getattr(init_plugin_manager, accessor) == ([tool] if as_list else tool)


@pytest.mark.parametrize("accessor", ["video_processor", "audio_processor"])
def test_processor_accessor_is_none_without_a_processor(init_plugin_manager: PluginManager, accessor: str) -> None:
    assert getattr(init_plugin_manager, accessor) is None


@pytest.mark.parametrize("third_party_first", [False, True], ids=["builtin_first", "third_party_first"])
def test_third_party_audio_processor_replaces_the_builtin_one(
    init_plugin_manager: PluginManager, third_party_first: bool
) -> None:
    third_party = make_tool("audio_processor_id")
    plugins = [
        (builtin_audio_convert, "vsview.app.tools.audio_convert"),
        (hook_plugin("vsview_get_audio_processor", third_party), "third_party"),
    ]
    for plugin, name in reversed(plugins) if third_party_first else plugins:
        init_plugin_manager.manager.register(plugin, name=name)

    assert init_plugin_manager.audio_processor is third_party


def test_builtin_audio_processor_is_used_alone(init_plugin_manager: PluginManager) -> None:
    init_plugin_manager.manager.register(builtin_audio_convert, name="vsview.app.tools.audio_convert")

    assert init_plugin_manager.audio_processor is BuiltinAudioConvert


def test_workspaces_only_lists_allowed_plugins(init_plugin_manager: PluginManager) -> None:
    editor, remote, other = make_tool("vsview_editor"), make_tool("vsview_remote"), make_tool("other_workspace")
    for name, workspace in [("vsview_editor", editor), ("vsview_remote", remote), ("other_plugin", other)]:
        init_plugin_manager.manager.register(hook_plugin("vsview_register_workspace", workspace), name=name)

    assert set(init_plugin_manager.workspaces) == {editor, remote}


def test_all_plugins_lists_every_kind_sorted_by_identifier(init_plugin_manager: PluginManager) -> None:
    registrations = [
        ("vsview_register_tooldock", "z_dock", None),
        ("vsview_register_toolpanel", "a_panel", None),
        ("vsview_register_workspace", "b_workspace", "vsview_editor"),
        ("vsview_get_video_processor", "m_video", None),
        ("vsview_get_audio_processor", "c_audio", None),
    ]
    for hook_name, identifier, name in registrations:
        init_plugin_manager.manager.register(hook_plugin(hook_name, make_tool(identifier)), name=name)

    identifiers = [p.identifier for p in init_plugin_manager.all_plugins]

    assert identifiers == ["a_panel", "b_workspace", "c_audio", "m_video", "z_dock"]


def test_accessors_raise_before_plugins_are_loaded() -> None:
    pm = PluginManager()

    with pytest.raises(RuntimeError, match="not loaded"):
        _ = pm.tooldocks


# Loading
def test_load_skips_a_broken_entry_point_and_keeps_the_others(load_plugins: LoadPlugins) -> None:
    tool = make_tool("third_party_tool")

    pm = load_plugins({"broken_tool": "vsview_missing_module:plugin", "third_party_tool": ToolDock(tool)})

    assert pm.loaded is True
    assert tool in pm.tooldocks
    assert pm.manager.has_plugin("broken_tool") is False


def test_load_skips_a_broken_first_party_tool_and_keeps_the_others(
    load_plugins: LoadPlugins, qtbot: QtBot, extra_tools_dir: Path
) -> None:
    (extra_tools_dir / "audit_import_error.py").write_text('raise ImportError("simulated broken tool")\n')
    (extra_tools_dir / "audit_invalid_hook.py").write_text(
        "from vsview.api import hookimpl\n\n\n@hookimpl\ndef vsview_register_tooldock(unexpected_arg): ...\n"
    )
    holder = CallbackHolder()
    PluginManager().call_when_loaded(holder.on_notify)

    pm = load_plugins({})

    assert pm.loaded is True
    assert FIRST_PARTY_IDENTIFIER in {p.identifier for p in pm.tooldocks}
    assert [name for name, _ in pm.manager.list_name_plugin() if "audit_" in name] == []
    qtbot.waitUntil(lambda: holder.calls == 1, timeout=2000)


def test_load_unregisters_a_plugin_whose_hook_raises(load_plugins: LoadPlugins) -> None:
    tool = make_tool("valid_tool")

    pm = load_plugins({"exploding": RaisingToolDock(), "valid": ToolDock(tool)})

    assert pm.manager.has_plugin("exploding") is False
    assert tool in pm.tooldocks


@pytest.mark.parametrize(
    "rejected",
    [
        pytest.param({"dotted": "bad.identifier"}, id="dot_in_identifier"),
        pytest.param({"colliding": FIRST_PARTY_IDENTIFIER}, id="reuses_first_party_identifier"),
        pytest.param({"shared_a": "shared_tool", "shared_b": "shared_tool"}, id="third_parties_share_identifier"),
    ],
)
def test_load_skips_plugins_with_a_rejected_identifier(load_plugins: LoadPlugins, rejected: dict[str, str]) -> None:
    keeper = make_tool("keeper_tool")
    rejected_tools = {name: make_tool(identifier) for name, identifier in rejected.items()}

    pm = load_plugins({"keeper": ToolDock(keeper)} | {name: ToolDock(t) for name, t in rejected_tools.items()})

    assert [name for name in rejected if pm.manager.has_plugin(name)] == []
    assert keeper in pm.tooldocks
    assert set(rejected_tools.values()).isdisjoint(pm.tooldocks)
    assert FIRST_PARTY_IDENTIFIER in {p.identifier for p in pm.tooldocks}


def test_load_unregisters_a_plugin_that_fails_while_being_integrated(load_plugins: LoadPlugins) -> None:
    broken, valid = make_tool("broken_tool", shortcuts=123), make_tool("valid_tool")

    pm = load_plugins({"broken": ToolDock(broken), "valid": ToolDock(valid)})

    assert pm.manager.has_plugin("broken") is False
    assert valid in pm.tooldocks


def test_failed_load_is_not_reported_as_loaded(
    qt_event_loop: QtEventLoop,
    qtbot: QtBot,
    fake_entry_points: Callable[[str, Sequence[EntryPoint]], None],
    mocker: MockerFixture,
) -> None:
    fake_entry_points("vsview", [])
    pm = PluginManager()
    mocker.patch.object(pm.manager, "add_hookspecs", side_effect=RuntimeError("simulated load failure"))
    early = CallbackHolder()
    pm.call_when_loaded(early.on_notify)

    pm.load()
    with pytest.raises(RuntimeError, match="simulated load failure"):
        pm.wait_for_loaded()

    assert (pm.loaded, pm.load_failed) == (False, True)
    qtbot.waitUntil(lambda: early.calls == 1, timeout=2000)
    late = CallbackHolder()
    pm.call_when_loaded(late.on_notify)
    qtbot.waitUntil(lambda: late.calls == 1, timeout=2000)


def test_plugin_accessors_open_after_a_failed_load(
    qt_event_loop: QtEventLoop,
    fake_entry_points: Callable[[str, Sequence[EntryPoint]], None],
    mocker: MockerFixture,
) -> None:
    fake_entry_points("vsview", [])
    mocker.patch(
        "vsview.app.plugins.manager.load_entrypoints", side_effect=RuntimeError("simulated entry point failure")
    )
    pm = PluginManager()

    pm.load()
    with pytest.raises(RuntimeError, match="simulated entry point failure"):
        pm.wait_for_loaded()

    assert pm.load_failed is True
    assert FIRST_PARTY_IDENTIFIER in {p.identifier for p in pm.tooldocks}


# Shortcuts
@pytest.mark.parametrize(
    ("action_ids", "expected"),
    [
        pytest.param(["my_plugin.one", "my_plugin.two"], ["my_plugin.one", "my_plugin.two"], id="all_prefixed"),
        pytest.param(["other_plugin.one"], [], id="wrong_prefix"),
        pytest.param(["my_plugin.good", "unprefixed"], ["my_plugin.good"], id="mixed"),
    ],
)
def test_load_registers_only_shortcuts_in_the_plugin_namespace(
    load_plugins: LoadPlugins, action_ids: list[str], expected: list[str]
) -> None:
    shortcuts = tuple(ActionDefinition(action_id, action_id, "") for action_id in action_ids)

    load_plugins({"my_plugin": ToolDock(make_tool("my_plugin", shortcuts=shortcuts))})

    assert [action_id for action_id in action_ids if action_id in ShortcutManager.definitions] == expected


# Settings
class OptionsModel(BaseModel):
    name: str = "default_name"
    count: int = 10


class StrictModel(BaseModel):
    required_val: int


def test_load_fills_the_global_settings_of_plugins_with_a_model(load_plugins: LoadPlugins) -> None:
    load_plugins(
        {
            "with_model": ToolDock(make_tool("with_model", global_model=OptionsModel)),
            "without_model": ToolDock(make_tool("without_model")),
        }
    )

    plugins = SettingsManager.global_settings.plugins
    assert plugins["with_model"] == OptionsModel()
    assert "without_model" not in plugins


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        pytest.param({"name": "custom", "count": 42}, OptionsModel(name="custom", count=42), id="valid_is_kept"),
        pytest.param({"count": "not_an_int"}, OptionsModel(), id="invalid_falls_back_to_defaults"),
    ],
)
def test_load_validates_stored_global_settings(
    load_plugins: LoadPlugins, stored: dict[str, Any], expected: OptionsModel
) -> None:
    SettingsManager.global_settings.plugins["options_tool"] = stored

    load_plugins({"options_tool": ToolDock(make_tool("options_tool", global_model=OptionsModel))})

    assert SettingsManager.global_settings.plugins["options_tool"] == expected


def test_load_keeps_stored_settings_when_the_model_has_no_defaults(load_plugins: LoadPlugins) -> None:
    SettingsManager.global_settings.plugins["strict_tool"] = {"required_val": "invalid"}

    load_plugins({"strict_tool": ToolDock(make_tool("strict_tool", global_model=StrictModel))})

    assert SettingsManager.global_settings.plugins["strict_tool"] == {"required_val": "invalid"}


def test_populate_default_settings_loads_plugins_and_fills_local_settings(
    qt_event_loop: QtEventLoop, fake_entry_points: Callable[[str, Mapping[str, Any]], None], tmp_path: Path
) -> None:
    fake_entry_points("vsview", {"local_tool": ToolDock(make_tool("local_tool", local_model=OptionsModel))})
    file_path = tmp_path / "test_script.vpy"
    pm = PluginManager()

    pm.populate_default_settings("local", file_path=file_path)

    assert pm.loaded is True
    assert SettingsManager.get_local_settings(file_path).plugins["local_tool"] == OptionsModel()


class GlobalAnnotatedModel(BaseModel):
    enabled: Annotated[bool, Checkbox(label="Enabled", text="Enable Feature")] = True


class LocalAnnotatedModel(BaseModel):
    active: Annotated[bool, Checkbox(label="Active", text="Activate Local Feature")] = False


def test_load_adds_plugin_settings_to_the_settings_dialog(load_plugins: LoadPlugins) -> None:
    configurable = make_tool("configurable_tool", global_model=GlobalAnnotatedModel, local_model=LocalAnnotatedModel)

    load_plugins({"configurable": ToolDock(configurable), "plain": ToolDock(make_tool("plain_tool"))})

    def plugin_entries(registry: list[Any]) -> list[tuple[str, str]]:
        return [
            (e.key, e.section) for e in registry if e.key.startswith(("plugins.configurable_tool.", "plugins.plain"))
        ]

    assert plugin_entries(SettingsDialog.global_settings_registry) == [
        ("plugins.configurable_tool.enabled", "Plugin - Configurable Tool")
    ]
    assert plugin_entries(SettingsDialog.local_settings_registry) == [
        ("plugins.configurable_tool.active", "Plugin - Configurable Tool")
    ]


# Plugin base metaclass
def test_plugin_base_meta_ignores_none_scope_on_tuple_length_mismatch() -> None:
    class DummyGlobalModel(BaseModel):
        flag: bool = True

    class DummyLocalModel(BaseModel):
        count: int = 42

    class ExtraType: ...

    class TriGenericWidgetPlugin[G: DummyGlobalModel, L: DummyLocalModel, E](WidgetPluginBase[G, L]):
        __plugin_base__ = True

    class ConcretePlugin(TriGenericWidgetPlugin[DummyGlobalModel, DummyLocalModel, ExtraType]):
        identifier = "concrete_plugin"

    assert ConcretePlugin.global_settings_model is DummyGlobalModel
    assert ConcretePlugin.local_settings_model is DummyLocalModel
    assert hasattr(ConcretePlugin, "None_settings_model") is False

    class QuadGenericNodeProcessor[N: vs.VideoNode, G: DummyGlobalModel, L: DummyLocalModel, E](NodeProcessor[N, G, L]):
        __plugin_base__ = True

    class ConcreteQuadNodeProcessor(
        QuadGenericNodeProcessor[vs.VideoNode, DummyGlobalModel, DummyLocalModel, ExtraType]
    ): ...

    assert ConcreteQuadNodeProcessor.global_settings_model is DummyGlobalModel
    assert ConcreteQuadNodeProcessor.local_settings_model is DummyLocalModel
    assert hasattr(ConcreteQuadNodeProcessor, "None_settings_model") is False
