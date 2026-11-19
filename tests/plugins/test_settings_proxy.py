from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from vsview.app.plugins._interface import _PluginSettingsStore, _SettingsProxy
from vsview.app.plugins.contracts import LocalSettingsModel
from vsview.app.settings import SettingsManager

pytestmark = [pytest.mark.unit]


class MockNestedModel(BaseModel):
    nest_attr: str = "nest_default"


class MockParentModel(BaseModel):
    attr1: str = "default1"
    nest: MockNestedModel = MockNestedModel()


class MockLevel3(BaseModel):
    val: int = 3


class MockLevel2(BaseModel):
    l3: MockLevel3 = MockLevel3()


class MockLevel1(BaseModel):
    l2: MockLevel2 = MockLevel2()


class MockGlobalSettings(BaseModel):
    attr1: str = "default1"
    attr2: int = 10


class MockLocalSettings(LocalSettingsModel):
    attr1: str | None = None
    attr2: int | None = None


class MockPlugin:
    identifier: ClassVar[str] = "test_plugin"
    global_settings_model: ClassVar[type[BaseModel] | None] = MockGlobalSettings
    local_settings_model: ClassVar[type[BaseModel] | None] = MockLocalSettings
    api: Any = None


@pytest.fixture
def script_path(tmp_path: Path) -> Path:
    return tmp_path / "test.vpy"


@pytest.fixture
def store(script_path: Path) -> _PluginSettingsStore:
    return _PluginSettingsStore(lambda: script_path)


def _local_plugins(script_path: Path) -> dict[str, Any]:
    return SettingsManager.get_local_settings(script_path).plugins


# _SettingsProxy
def test_proxy_reads_the_model() -> None:
    proxy = _SettingsProxy(MockGlobalSettings(attr1="a"))

    assert proxy.attr1 == "a"
    assert proxy.attr2 == 10


def test_proxy_write_is_stored_in_the_model() -> None:
    model = MockGlobalSettings()
    proxy = _SettingsProxy(model)

    proxy.attr1 = "new_value"

    assert model.attr1 == "new_value"


def test_proxy_write_stores_the_validated_value() -> None:
    model = MockGlobalSettings()
    proxy = _SettingsProxy(model)

    proxy.attr2 = "42"

    assert model.attr2 == 42


def test_proxy_equality() -> None:
    proxy1 = _SettingsProxy(MockGlobalSettings(attr1="a"))
    proxy2 = _SettingsProxy(MockGlobalSettings(attr1="a"))

    assert proxy1 == proxy2
    assert proxy1 == MockGlobalSettings(attr1="a")
    assert proxy1 != MockGlobalSettings(attr1="b")
    assert proxy1 != "not a proxy"


def test_proxy_rejects_unknown_attributes() -> None:
    model = MockGlobalSettings()
    proxy = _SettingsProxy(model)

    with pytest.raises(AttributeError, match="unknown_attr"):
        proxy.unknown_attr = 5

    assert model == MockGlobalSettings()


def test_proxy_write_of_the_wrong_type_leaves_the_model_unchanged() -> None:
    model = MockGlobalSettings(attr2=7)
    proxy = _SettingsProxy(model)

    with pytest.raises(ValidationError, match="attr2"):
        proxy.attr2 = "not a number"

    assert model == MockGlobalSettings(attr2=7)


def test_proxy_shallow_copy_writes_to_the_same_model() -> None:
    model = MockGlobalSettings(attr1="copied")
    proxy = _SettingsProxy(model)

    copied = copy.copy(proxy)
    copied.attr2 = 20

    assert copied == proxy
    assert model.attr2 == 20


def test_proxy_nested_write_is_stored_in_the_model() -> None:
    model = MockParentModel()

    _SettingsProxy(model).nest.nest_attr = "new_nest_value"

    assert model.nest.nest_attr == "new_nest_value"


def test_proxy_deeply_nested_write_is_stored_in_the_model() -> None:
    model = MockLevel1()

    _SettingsProxy(model).l2.l3.val = 42

    assert model.l2.l3.val == 42


# _PluginSettingsStore
def test_store_reads_the_stored_raw_settings(store: _PluginSettingsStore) -> None:
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "from_storage"}

    proxy = store.get_proxy(MockPlugin(), "global")

    assert proxy == MockGlobalSettings(attr1="from_storage")


def test_store_uses_the_defaults_when_the_stored_settings_are_invalid(store: _PluginSettingsStore) -> None:
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr2": "not a number"}

    proxy = store.get_proxy(MockPlugin(), "global")

    assert proxy == MockGlobalSettings()
    assert SettingsManager.global_settings.plugins["test_plugin"] == MockGlobalSettings()


def test_store_returns_none_for_a_scope_without_a_model(store: _PluginSettingsStore) -> None:
    class GlobalOnlyPlugin(MockPlugin):
        local_settings_model = None

    assert store.get_proxy(GlobalOnlyPlugin(), "local") is None


def test_store_write_is_seen_by_other_plugin_instances_and_workspaces(script_path: Path) -> None:
    panel_store = _PluginSettingsStore(lambda: script_path)
    other_workspace_store = _PluginSettingsStore(lambda: script_path)
    panel, dock = MockPlugin(), MockPlugin()
    dock_global = panel_store.get_proxy(dock, "global")
    dock_local = other_workspace_store.get_proxy(dock, "local")

    panel_store.update(panel, "global", attr1="global_write")
    panel_store.update(panel, "local", attr2=200)

    assert dock_global == MockGlobalSettings(attr1="global_write")
    assert other_workspace_store.get_proxy(dock, "global") == MockGlobalSettings(attr1="global_write")
    assert dock_local is not None
    assert dock_local.attr2 == 200


def test_store_local_reads_unset_fields_from_global(store: _PluginSettingsStore, script_path: Path) -> None:
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "global_val", "attr2": 100}
    _local_plugins(script_path)["test_plugin"] = {"attr2": 200}

    local = store.get_proxy(MockPlugin(), "local")

    assert local is not None
    assert (local.attr1, local.attr2) == ("global_val", 200)


def test_store_global_write_is_seen_through_local(store: _PluginSettingsStore) -> None:
    plugin = MockPlugin()
    local = store.get_proxy(plugin, "local")

    store.update(plugin, "global", attr1="changed")

    assert local is not None
    assert local.attr1 == "changed"


def test_store_local_write_stores_only_the_override(store: _PluginSettingsStore, script_path: Path) -> None:
    plugin = MockPlugin()
    local = store.get_proxy(plugin, "local")
    assert local is not None

    local.attr2 = 5

    assert _local_plugins(script_path)["test_plugin"] == MockLocalSettings(attr2=5)
    assert SettingsManager.global_settings.plugins["test_plugin"] == MockGlobalSettings()


def test_store_nested_local_override_keeps_reading_other_fields_from_global(
    store: _PluginSettingsStore, script_path: Path
) -> None:
    class SubModel(BaseModel):
        opacity: float | None = None
        color: str | None = None

    class NestedLocalSettings(LocalSettingsModel):
        visual: SubModel = SubModel()

    class NestedGlobalSettings(BaseModel):
        visual: SubModel = SubModel(opacity=1.0, color="red")

    class NestedPlugin(MockPlugin):
        identifier = "nested_plugin"
        global_settings_model = NestedGlobalSettings
        local_settings_model = NestedLocalSettings

    plugin = NestedPlugin()
    local = store.get_proxy(plugin, "local")
    assert local is not None

    local.visual.opacity = 0.5
    store.update(plugin, "global", visual=SubModel(opacity=1.0, color="blue"))

    assert (local.visual.opacity, local.visual.color) == (0.5, "blue")
    assert _local_plugins(script_path)["nested_plugin"] == NestedLocalSettings(visual=SubModel(opacity=0.5))


def test_store_write_into_a_global_fallback_is_refused(store: _PluginSettingsStore, script_path: Path) -> None:
    class Visual(BaseModel):
        opacity: float = 1.0

    class OptionalVisualLocal(LocalSettingsModel):
        visual: Visual | None = None

    class VisualGlobal(BaseModel):
        visual: Visual = Visual()

    class OptionalVisualPlugin(MockPlugin):
        identifier = "optional_visual_plugin"
        global_settings_model = VisualGlobal
        local_settings_model = OptionalVisualLocal

    local = store.get_proxy(OptionalVisualPlugin(), "local")
    assert local is not None

    with pytest.raises(TypeError, match="opacity"):
        local.visual.opacity = 0.5

    assert local.visual.opacity == 1.0
    assert SettingsManager.global_settings.plugins["optional_visual_plugin"] == VisualGlobal()
    assert _local_plugins(script_path)["optional_visual_plugin"] == OptionalVisualLocal()


def test_store_in_place_change_of_a_global_fallback_list_keeps_the_global_list(store: _PluginSettingsStore) -> None:
    class ListGlobal(BaseModel):
        items: list[int] = [1, 2]

    class ListLocal(LocalSettingsModel):
        items: list[int] | None = None

    class ListPlugin(MockPlugin):
        identifier = "list_plugin"
        global_settings_model = ListGlobal
        local_settings_model = ListLocal

    local = store.get_proxy(ListPlugin(), "local")
    assert local is not None

    local.items.append(3)

    assert SettingsManager.global_settings.plugins["list_plugin"] == ListGlobal(items=[1, 2])


def test_store_update_sets_a_nested_field_by_dotted_key(store: _PluginSettingsStore) -> None:
    class NestedPlugin(MockPlugin):
        identifier = "nested_plugin"
        global_settings_model = MockParentModel

    store.update(NestedPlugin(), "global", **{"nest.nest_attr": "dotted"})

    assert SettingsManager.global_settings.plugins["nested_plugin"] == MockParentModel(
        nest=MockNestedModel(nest_attr="dotted")
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"attr2": "not a number"},
        {"attr1": "valid", "attr2": "not a number"},
    ],
)
def test_store_update_with_a_wrong_type_changes_nothing(store: _PluginSettingsStore, updates: dict[str, Any]) -> None:
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "kept", "attr2": 7}
    proxy = store.get_proxy(MockPlugin(), "global")

    with pytest.raises(ValidationError, match="attr2"):
        store.update(MockPlugin(), "global", **updates)

    assert proxy == MockGlobalSettings(attr1="kept", attr2=7)


def test_store_update_without_a_model_is_refused(store: _PluginSettingsStore) -> None:
    class GlobalOnlyPlugin(MockPlugin):
        local_settings_model = None

    with pytest.raises(ValueError, match="local_settings_model"):
        store.update(GlobalOnlyPlugin(), "local", attr1="x")


def test_store_keeps_local_writes_without_a_file_path() -> None:
    store = _PluginSettingsStore(lambda: None)

    store.update(MockPlugin(), "local", attr2=5)

    assert store.get_proxy(MockPlugin(), "local") == MockLocalSettings(attr2=5)


def test_store_moves_session_settings_to_the_file_when_a_path_appears(script_path: Path) -> None:
    path: list[Path | None] = [None]
    store = _PluginSettingsStore(lambda: path[0])
    store.update(MockPlugin(), "local", attr2=5)

    path[0] = script_path

    assert store.get_proxy(MockPlugin(), "local") == MockLocalSettings(attr2=5)
    assert _local_plugins(script_path)["test_plugin"] == MockLocalSettings(attr2=5)


def test_store_keeps_the_stored_file_settings_when_a_path_appears(script_path: Path) -> None:
    _local_plugins(script_path)["test_plugin"] = {"attr2": 200}
    path: list[Path | None] = [None]
    store = _PluginSettingsStore(lambda: path[0])
    store.update(MockPlugin(), "local", attr2=5)

    path[0] = script_path

    assert store.get_proxy(MockPlugin(), "local") == MockLocalSettings(attr2=200)
