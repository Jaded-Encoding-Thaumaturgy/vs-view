from __future__ import annotations

import gc
import logging
import weakref
from collections.abc import Generator
from concurrent.futures import Future
from pathlib import Path
from typing import Annotated, Any

import pytest
from jetpytools import inject_self
from jetpytools.types.utils import _self_objects_cache
from pydantic import BaseModel
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

from vsview.app.plugins import specs
from vsview.app.plugins.manager import Notifier, PluginManager, _safe_validate, ensure_loaded
from vsview.app.settings.action import ActionDefinition
from vsview.app.settings.dialog import SettingsDialog
from vsview.app.settings.manager import SettingsManager
from vsview.app.settings.metadata import Checkbox
from vsview.app.settings.shortcuts import ShortcutManager
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt]


# Isolation Fixtures
@pytest.fixture(autouse=True)
def isolate_plugin_manager_singleton() -> Generator[None]:
    """Ensure a fresh PluginManager singleton state for every test."""
    _self_objects_cache.pop(SettingsManager, None)
    type(PluginManager)._instances.pop(PluginManager, None)
    yield
    _self_objects_cache.pop(SettingsManager, None)
    type(PluginManager)._instances.pop(PluginManager, None)


@pytest.fixture(autouse=True)
def isolate_shortcut_definitions() -> Generator[None]:
    """Prevent tests from leaking registered shortcut definitions."""
    orig = ShortcutManager.definitions.copy()
    try:
        yield
    finally:
        ShortcutManager.definitions.clear()
        ShortcutManager.definitions.update(orig)


def _register_mock_tool(pm: PluginManager, tool_cls: type) -> None:
    """Helper to cleanly register mock plugins via Pluggy hookspec without monkeypatching all_plugins."""

    class _ToolImpl:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> type:
            return tool_cls

    pm.manager.register(_ToolImpl(), name=getattr(tool_cls, "identifier", None))
    pm.__dict__.pop("tooldocks", None)  # pyright: ignore[reportAttributeAccessIssue]


@pytest.fixture
def isolate_settings_dialog_registries() -> Generator[None]:
    """Prevent tests modifying SettingsDialog registries from leaking state."""
    orig_global = list(SettingsDialog.global_settings_registry)
    orig_local = list(SettingsDialog.local_settings_registry)
    try:
        yield
    finally:
        SettingsDialog.global_settings_registry.clear()
        SettingsDialog.global_settings_registry.extend(orig_global)
        SettingsDialog.local_settings_registry.clear()
        SettingsDialog.local_settings_registry.extend(orig_local)


# Notifier Tests
class DummyCallbackHolder:
    def __init__(self) -> None:
        self.invoked = False

    def on_notify(self) -> None:
        self.invoked = True


def test_notifier_registration_function_invoked_on_notify(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    invoked = False

    def my_callback() -> None:
        nonlocal invoked
        invoked = True

    notifier.register(my_callback)
    notifier.notify()

    qtbot.waitUntil(lambda: invoked, timeout=2000)
    assert invoked is True


def test_notifier_registration_bound_method_allows_garbage_collection(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    holder = DummyCallbackHolder()
    weak_holder = weakref.ref(holder)

    notifier.register(holder.on_notify)
    del holder
    gc.collect()

    # The holder must be cleanly garbage-collected, proving the bound method was weakly referenced
    assert weak_holder() is None

    # Notifier should safely execute notify without error when callback is dead
    notifier.notify()


def test_notifier_notify_invokes_callbacks_on_loop(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    holder = DummyCallbackHolder()
    func_called = [False]

    def free_callback() -> None:
        func_called[0] = True

    notifier.register(free_callback)
    notifier.register(holder.on_notify)

    notifier.notify()

    qtbot.waitUntil(lambda: func_called[0] and holder.invoked, timeout=2000)
    assert func_called[0] is True
    assert holder.invoked is True


def test_notifier_register_when_already_ready_invokes_immediately(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    called = [False]

    def callback() -> None:
        called[0] = True

    # Transition notifier to ready state
    notifier.notify()

    # Registering while ready should schedule callback immediately
    notifier.register(callback)

    qtbot.waitUntil(lambda: called[0], timeout=2000)
    assert called[0] is True


def test_notifier_multiple_notify_is_idempotent(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    call_count = [0]

    def callback() -> None:
        call_count[0] += 1

    notifier.register(callback)

    notifier.notify()
    notifier.notify()
    notifier.notify()

    qtbot.waitUntil(lambda: call_count[0] == 1, timeout=2000)
    qtbot.wait(50)
    assert call_count[0] == 1


def test_notifier_safely_ignores_garbage_collected_callbacks(qt_event_loop: QtEventLoop, qtbot: QtBot) -> None:
    notifier = Notifier()
    survivor_called = [False]

    def register_dead_callback() -> None:
        temp_holder = DummyCallbackHolder()
        notifier.register(temp_holder.on_notify)

    register_dead_callback()
    gc.collect()

    def survivor_callback() -> None:
        survivor_called[0] = True

    notifier.register(survivor_callback)

    # Calling notify should not raise an error even though first callback is dead
    notifier.notify()

    qtbot.waitUntil(lambda: survivor_called[0], timeout=2000)
    assert survivor_called[0] is True


# ensure_loaded Decorator Tests
class DummyLoadedTarget:
    def __init__(self, entrypoints_loaded: bool = False, loaded: bool = False) -> None:
        self._entry_points_loaded = entrypoints_loaded
        self.loaded = loaded
        self.load_called = False
        self.wait_called = False

    @inject_self
    def load(self) -> None:
        self.load_called = True
        self.loaded = True

    @inject_self
    def wait_for_loaded(self) -> None:
        self.wait_called = True

    @ensure_loaded("entrypoints")  # pyright: ignore[reportArgumentType]
    def run_entrypoints(self, val: int = 1) -> int:
        return val * 10

    @ensure_loaded("wait")  # pyright: ignore[reportArgumentType]
    def run_wait(self, msg: str = "ok") -> str:
        return f"result: {msg}"


def test_ensure_loaded_entrypoints_raises_when_false() -> None:
    target = DummyLoadedTarget(entrypoints_loaded=False)

    with pytest.raises(RuntimeError, match="PluginManager is not loaded yet"):
        target.run_entrypoints(5)


def test_ensure_loaded_entrypoints_allows_call_when_true() -> None:
    target = DummyLoadedTarget(entrypoints_loaded=True)

    result = target.run_entrypoints(5)
    assert result == 50


def test_ensure_loaded_wait_triggers_load_and_wait_when_not_loaded() -> None:
    target = DummyLoadedTarget(loaded=False)

    result = target.run_wait("test")
    assert result == "result: test"
    assert target.load_called is True
    assert target.wait_called is True


def test_ensure_loaded_wait_does_not_trigger_load_when_already_loaded() -> None:
    target = DummyLoadedTarget(loaded=True)

    result = target.run_wait("test")
    assert result == "result: test"
    assert target.load_called is False
    assert target.wait_called is False


# PluginManager Properties & Accessors Tests
class MockToolDock:
    identifier = "dock_id"


class MockToolPanel:
    identifier = "panel_id"


class MockVideoProcessor:
    identifier = "video_processor_id"


class MockAudioProcessor:
    identifier = "audio_processor_id"


class MockEditorWorkspace:
    identifier = "vsview_editor"


class MockRemoteWorkspace:
    identifier = "vsview_remote"


class MockDisallowedWorkspace:
    identifier = "unauthorized_workspace"


def test_plugin_manager_tooldocks_accessor(init_plugin_manager: PluginManager) -> None:
    class DockPlugin:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> type[MockToolDock]:
            return MockToolDock

    init_plugin_manager.manager.register(DockPlugin())
    assert init_plugin_manager.tooldocks == [MockToolDock]  # type: ignore


def test_plugin_manager_toolpanels_accessor(init_plugin_manager: PluginManager) -> None:
    class PanelPlugin:
        @specs.hookimpl
        def vsview_register_toolpanel(self) -> type[MockToolPanel]:
            return MockToolPanel

    init_plugin_manager.manager.register(PanelPlugin())
    assert init_plugin_manager.toolpanels == [MockToolPanel]  # type: ignore


def test_plugin_manager_video_processor_accessor_none_when_empty(init_plugin_manager: PluginManager) -> None:
    assert init_plugin_manager.video_processor is None


def test_plugin_manager_video_processor_accessor_registered(init_plugin_manager: PluginManager) -> None:
    class VideoProcessorPlugin:
        @specs.hookimpl
        def vsview_get_video_processor(self) -> type[MockVideoProcessor]:
            return MockVideoProcessor

    init_plugin_manager.manager.register(VideoProcessorPlugin())
    assert init_plugin_manager.video_processor is MockVideoProcessor  # type: ignore


def test_plugin_manager_audio_processor_accessor_none_when_empty(init_plugin_manager: PluginManager) -> None:
    assert init_plugin_manager.audio_processor is None


def test_plugin_manager_audio_processor_accessor_registered(init_plugin_manager: PluginManager) -> None:
    class AudioProcessorPlugin:
        @specs.hookimpl
        def vsview_get_audio_processor(self) -> type[MockAudioProcessor]:
            return MockAudioProcessor

    init_plugin_manager.manager.register(AudioProcessorPlugin())
    assert init_plugin_manager.audio_processor is MockAudioProcessor  # type: ignore


def test_plugin_manager_workspaces_filters_unallowed_plugins(init_plugin_manager: PluginManager) -> None:
    class EditorPlugin:
        @specs.hookimpl
        def vsview_register_workspace(self) -> type[MockEditorWorkspace]:
            return MockEditorWorkspace

    class RemotePlugin:
        @specs.hookimpl
        def vsview_register_workspace(self) -> type[MockRemoteWorkspace]:
            return MockRemoteWorkspace

    class DisallowedPlugin:
        @specs.hookimpl
        def vsview_register_workspace(self) -> type[MockDisallowedWorkspace]:
            return MockDisallowedWorkspace

    init_plugin_manager.manager.register(EditorPlugin(), name="vsview_editor")
    init_plugin_manager.manager.register(RemotePlugin(), name="vsview_remote")
    init_plugin_manager.manager.register(DisallowedPlugin(), name="unauthorized_plugin")

    workspaces = init_plugin_manager.workspaces
    assert len(workspaces) == 2
    assert MockDisallowedWorkspace not in workspaces
    assert MockRemoteWorkspace in workspaces
    assert MockEditorWorkspace in workspaces


def test_plugin_manager_all_plugins_returns_sorted_by_identifier(init_plugin_manager: PluginManager) -> None:
    class MockDock:
        identifier = "z_dock"

    class MockPanel:
        identifier = "a_panel"

    class MockWorkspace:
        identifier = "b_workspace"

    class MockVP:
        identifier = "m_video"

    class MockAP:
        identifier = "c_audio"

    class DockPlugin:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> type[MockDock]:
            return MockDock

    class PanelPlugin:
        @specs.hookimpl
        def vsview_register_toolpanel(self) -> type[MockPanel]:
            return MockPanel

    class WorkspacePlugin:
        @specs.hookimpl
        def vsview_register_workspace(self) -> type[MockWorkspace]:
            return MockWorkspace

    class VPPlugin:
        @specs.hookimpl
        def vsview_get_video_processor(self) -> type[MockVP]:
            return MockVP

    class APPlugin:
        @specs.hookimpl
        def vsview_get_audio_processor(self) -> type[MockAP]:
            return MockAP

    init_plugin_manager.manager.register(DockPlugin())
    init_plugin_manager.manager.register(PanelPlugin())
    init_plugin_manager.manager.register(WorkspacePlugin(), name="vsview_editor")
    init_plugin_manager.manager.register(VPPlugin())
    init_plugin_manager.manager.register(APPlugin())

    plugins = init_plugin_manager.all_plugins
    identifiers = [p.identifier for p in plugins]
    assert identifiers == ["a_panel", "b_workspace", "c_audio", "m_video", "z_dock"]


def test_plugin_manager_all_plugins_handles_none_processors(init_plugin_manager: PluginManager) -> None:
    class MockDock:
        identifier = "dock_only"

    class DockPlugin:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> type[MockDock]:
            return MockDock

    init_plugin_manager.manager.register(DockPlugin())

    assert init_plugin_manager.video_processor is None
    assert init_plugin_manager.audio_processor is None
    assert init_plugin_manager.all_plugins == [MockDock]


# Plugin Collision Handling in _load_worker Tests
def test_plugin_identifiers_catches_hook_inspection_exceptions_safely(
    init_plugin_manager: PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    class ExplodingPlugin:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> Any:
            raise RuntimeError("Exploding hook inspection")

    class ValidPluginObj:
        identifier = "valid_plugin_id"

    class ValidPlugin:
        @specs.hookimpl
        def vsview_register_tooldock(self) -> type[ValidPluginObj]:
            return ValidPluginObj

    init_plugin_manager.manager.register(ExplodingPlugin(), name="exploding_plugin")
    init_plugin_manager.manager.register(ValidPlugin(), name="valid_plugin")

    with caplog.at_level(logging.ERROR):
        identifiers = init_plugin_manager._plugin_identifiers

    assert "valid_plugin_id" in identifiers
    assert identifiers["valid_plugin_id"] == "valid_plugin"
    assert "Failed to inspect hook 'vsview_register_tooldock' in plugin 'exploding_plugin'" in caplog.text


def test_third_party_collision_unregistered(
    qt_event_loop: QtEventLoop, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    pm = PluginManager()

    class ThirdPartyCollidingPlugin:
        identifier = "jet_vsview_colorpicker"
        display_name = "Colliding Color Picker"
        global_settings_model = None
        local_settings_model = None

        @specs.hookimpl
        def vsview_register_tooldock(self) -> type[ThirdPartyCollidingPlugin]:
            return ThirdPartyCollidingPlugin

    colliding_instance = ThirdPartyCollidingPlugin()

    def fake_load_entrypoints(_group: str) -> int:
        pm.manager.register(colliding_instance, name="third_party_package")
        return 1

    mocker.patch.object(pm.manager, "load_setuptools_entrypoints", side_effect=fake_load_entrypoints)

    with caplog.at_level(logging.WARNING):
        pm.load()
        pm.wait_for_loaded()

    assert pm.manager.is_registered(colliding_instance) is False
    assert (
        "Skipping third-party plugin 'third_party_package' because it reuses first-party plugin identifier "
        "'jet_vsview_colorpicker'" in caplog.text
    )


def test_load_worker_catches_entrypoint_loading_failure(
    qt_event_loop: QtEventLoop, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    pm = PluginManager()

    mocker.patch.object(
        pm.manager,
        "load_setuptools_entrypoints",
        side_effect=RuntimeError("Disk read error while loading entrypoints"),
    )

    with caplog.at_level(logging.ERROR):
        pm.load()
        pm.wait_for_loaded()

    assert pm._entry_points_loaded is True
    assert "Couldn't load entry points!" in caplog.text


# Shortcut Registration Tests
def test_register_shortcuts_valid_prefix(init_plugin_manager: PluginManager, mocker: MockerFixture) -> None:
    action_1 = ActionDefinition("my_plugin.action1", "Action 1", "Ctrl+1")
    action_2 = ActionDefinition("my_plugin.action2", "Action 2", "Ctrl+2")

    class MockPluginWithShortcuts:
        identifier = "my_plugin"
        shortcuts = (action_1, action_2)

    spy = mocker.spy(ShortcutManager, "register_definitions")
    _register_mock_tool(init_plugin_manager, MockPluginWithShortcuts)

    init_plugin_manager._register_shortcuts()

    spy.assert_called_once_with([action_1, action_2])
    assert "my_plugin.action1" in ShortcutManager.definitions
    assert "my_plugin.action2" in ShortcutManager.definitions


def test_register_shortcuts_invalid_prefix_warns_and_ignores(
    init_plugin_manager: PluginManager, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    bad_action = ActionDefinition("other_plugin.action", "Bad Action", "Ctrl+B")

    class MockPluginWithBadShortcut:
        identifier = "my_plugin_bad"
        shortcuts = (bad_action,)

    spy = mocker.spy(ShortcutManager, "register_definitions")
    _register_mock_tool(init_plugin_manager, MockPluginWithBadShortcut)

    with caplog.at_level(logging.WARNING):
        init_plugin_manager._register_shortcuts()

    spy.assert_not_called()
    assert (
        "Plugin 'my_plugin_bad' has shortcut 'other_plugin.action' without proper namespace prefix. "
        "Expected prefix: 'my_plugin_bad.'. Shortcut will be ignored." in caplog.text
    )


def test_register_shortcuts_mixed_valid_and_invalid(
    init_plugin_manager: PluginManager, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    good_action = ActionDefinition("my_plugin_mixed.good", "Good Action", "Ctrl+G")
    bad_action = ActionDefinition("unprefixed_action", "Bad Action", "Ctrl+U")

    class MockPluginWithMixed:
        identifier = "my_plugin_mixed"
        shortcuts = (good_action, bad_action)

    spy = mocker.spy(ShortcutManager, "register_definitions")
    _register_mock_tool(init_plugin_manager, MockPluginWithMixed)

    with caplog.at_level(logging.WARNING):
        init_plugin_manager._register_shortcuts()

    spy.assert_called_once_with([good_action])
    assert "Shortcut will be ignored." in caplog.text
    assert "my_plugin_mixed.good" in ShortcutManager.definitions


def test_register_shortcuts_without_shortcuts_attr_handled_cleanly(
    init_plugin_manager: PluginManager, mocker: MockerFixture
) -> None:
    class MockPluginNoShortcuts:
        identifier = "plain_plugin"

    spy = mocker.spy(ShortcutManager, "register_definitions")
    _register_mock_tool(init_plugin_manager, MockPluginNoShortcuts)

    init_plugin_manager._register_shortcuts()
    spy.assert_not_called()


# Settings Extraction and Validation Tests
class DummySettingsModel(BaseModel):
    name: str = "default_name"
    count: int = 10


class StrictSettingsModel(BaseModel):
    required_val: int


def test_safe_validate_valid_dict() -> None:
    result = _safe_validate(DummySettingsModel, {"name": "custom", "count": 42}, "test_plugin")
    assert result == DummySettingsModel(name="custom", count=42)


def test_safe_validate_invalid_dict_falls_back_to_defaults(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = _safe_validate(DummySettingsModel, {"count": "not_an_int"}, "test_plugin")

    assert result == DummySettingsModel(name="default_name", count=10)
    assert "The plugin 'test_plugin' has invalid settings:" in caplog.text
    assert "Trying default values..." in caplog.text


def test_safe_validate_unrecoverable_defaults_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = _safe_validate(StrictSettingsModel, {"required_val": "invalid_val"}, "strict_plugin")

    assert result is None
    assert "The plugin 'strict_plugin' has invalid settings:" in caplog.text
    assert "Couldn't create default settings values for 'strict_plugin'" in caplog.text


def test_populate_default_settings_global_scope(init_plugin_manager: PluginManager) -> None:
    class DummyGlobalModel(BaseModel):
        opt: str = "glob"

    class MockPlugin:
        identifier = "glob_plugin"
        global_settings_model = DummyGlobalModel
        local_settings_model = None

    _register_mock_tool(init_plugin_manager, MockPlugin)

    init_plugin_manager._populate_default_settings("global")

    assert "glob_plugin" in SettingsManager.global_settings.plugins
    assert SettingsManager.global_settings.plugins["glob_plugin"] == DummyGlobalModel(opt="glob")


def test_populate_default_settings_local_scope_with_file_path(
    init_plugin_manager: PluginManager, tmp_path: Path
) -> None:
    class DummyLocalModel(BaseModel):
        local_opt: int = 123

    class MockPlugin:
        identifier = "local_plugin"
        global_settings_model = None
        local_settings_model = DummyLocalModel

    _register_mock_tool(init_plugin_manager, MockPlugin)

    file_path = tmp_path / "test_script.vpy"
    init_plugin_manager._populate_default_settings("local", file_path=file_path)

    local_settings = SettingsManager.get_local_settings(file_path)
    assert "local_plugin" in local_settings.plugins
    assert local_settings.plugins["local_plugin"] == DummyLocalModel(local_opt=123)


def test_populate_default_settings_preserves_existing_model(init_plugin_manager: PluginManager) -> None:
    class DummyGlobalModel(BaseModel):
        opt: str = "default_opt"

    class MockPlugin:
        identifier = "existing_plugin"
        global_settings_model = DummyGlobalModel
        local_settings_model = None

    SettingsManager.global_settings.plugins["existing_plugin"] = DummyGlobalModel(opt="preserved_opt")
    _register_mock_tool(init_plugin_manager, MockPlugin)

    init_plugin_manager._populate_default_settings("global")

    assert SettingsManager.global_settings.plugins["existing_plugin"] == DummyGlobalModel(opt="preserved_opt")


def test_populate_default_settings_skips_when_model_is_none(init_plugin_manager: PluginManager) -> None:
    class MockPlugin:
        identifier = "none_model_plugin"
        global_settings_model = None
        local_settings_model = None

    _register_mock_tool(init_plugin_manager, MockPlugin)

    init_plugin_manager._populate_default_settings("global")
    assert "none_model_plugin" not in SettingsManager.global_settings.plugins


def test_populate_default_settings_skips_when_safe_validate_fails(init_plugin_manager: PluginManager) -> None:
    class MockPlugin:
        identifier = "failing_model_plugin"
        global_settings_model = StrictSettingsModel
        local_settings_model = None

    SettingsManager.global_settings.plugins["failing_model_plugin"] = {"required_val": "invalid"}
    _register_mock_tool(init_plugin_manager, MockPlugin)

    init_plugin_manager._populate_default_settings("global")
    # Because validation failed and StrictSettingsModel has no default constructor, value wasn't replaced with model
    assert not isinstance(SettingsManager.global_settings.plugins.get("failing_model_plugin"), StrictSettingsModel)


def test_populate_default_settings_public_method_populates_settings(
    init_plugin_manager: PluginManager,
) -> None:
    class PublicMethodGlobalModel(BaseModel):
        val: int = 999

    class MockPublicPlugin:
        identifier = "public_method_plugin"
        global_settings_model = PublicMethodGlobalModel
        local_settings_model = None

    fut = Future[None]()
    fut.set_result(None)
    init_plugin_manager._load_future = fut

    _register_mock_tool(init_plugin_manager, MockPublicPlugin)

    # Calling public method executes populate_default_settings against real PluginManager
    init_plugin_manager.populate_default_settings("global")

    assert "public_method_plugin" in SettingsManager.global_settings.plugins
    assert SettingsManager.global_settings.plugins["public_method_plugin"] == PublicMethodGlobalModel(val=999)


class GlobalAnnotatedModel(BaseModel):
    enabled: Annotated[bool, Checkbox(label="Enabled", text="Enable Feature")] = True


class LocalAnnotatedModel(BaseModel):
    active: Annotated[bool, Checkbox(label="Active", text="Activate Local Feature")] = False


def test_construct_settings_registry(
    init_plugin_manager: PluginManager,
    isolate_settings_dialog_registries: None,
) -> None:
    class MockConfigurablePlugin:
        identifier = "configurable_tool"
        display_name = "Configurable Tool"
        global_settings_model = GlobalAnnotatedModel
        local_settings_model = LocalAnnotatedModel

    class MockNoSettingsPlugin:
        identifier = "no_settings_tool"
        display_name = "No Settings Tool"
        global_settings_model = None
        local_settings_model = None

    _register_mock_tool(init_plugin_manager, MockConfigurablePlugin)
    _register_mock_tool(init_plugin_manager, MockNoSettingsPlugin)

    init_plugin_manager._construct_settings_registry()

    assert "configurable_tool" in SettingsManager.global_settings.plugins
    assert SettingsManager.global_settings.plugins["configurable_tool"] == GlobalAnnotatedModel(enabled=True)

    global_entries = [
        e for e in SettingsDialog.global_settings_registry if e.key == "plugins.configurable_tool.enabled"
    ]
    assert len(global_entries) == 1
    assert global_entries[0].section == "Plugin - Configurable Tool"

    local_entries = [e for e in SettingsDialog.local_settings_registry if e.key == "plugins.configurable_tool.active"]
    assert len(local_entries) == 1
    assert local_entries[0].section == "Plugin - Configurable Tool"


# PluginManager Lifecycle & Integration Tests
def test_plugin_manager_loaded_property() -> None:
    pm = PluginManager()
    assert pm.loaded is False

    fut = Future[None]()
    pm._load_future = fut
    assert pm.loaded is False

    fut.set_result(None)
    assert pm.loaded is True


def test_plugin_manager_load_idempotent(mocker: MockerFixture) -> None:
    pm = PluginManager()
    fut = Future[None]()
    fut.set_result(None)

    mock_load_worker = mocker.patch.object(pm, "_load_worker", return_value=fut)

    pm.load()
    assert mock_load_worker.call_count == 1
    assert pm._load_future is fut

    # Second call should be a no-op
    pm.load()
    assert mock_load_worker.call_count == 1


def test_plugin_manager_wait_for_loaded() -> None:
    pm = PluginManager()
    fut = Future[None]()
    fut.set_result(None)
    pm._load_future = fut

    pm.wait_for_loaded()
    assert pm._load_future.done() is True


def test_plugin_manager_call_when_loaded_delegates_to_notifier(
    init_plugin_manager: PluginManager, qt_event_loop: QtEventLoop, qtbot: QtBot
) -> None:
    called = [False]

    def callback() -> None:
        called[0] = True

    init_plugin_manager.call_when_loaded(callback)
    init_plugin_manager._notifier.notify()

    qtbot.waitUntil(lambda: called[0], timeout=2000)
    assert called[0] is True
