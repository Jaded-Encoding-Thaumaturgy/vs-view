from __future__ import annotations

from pathlib import Path
from typing import cast, override
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from pytest_mock import MockerFixture

from vsview.app.plugins._interface import _PluginSettingsStore, _SettingsProxy
from vsview.app.plugins.contracts import LocalSettingsModel
from vsview.app.settings import SettingsManager
from vsview.app.workspace import BaseGenericFileWorkspace

pytestmark = [pytest.mark.unit]


# Mock models for testing
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

    @override
    def resolve(self, global_settings: BaseModel) -> MockLocalSettings:
        resolved = self.model_copy()
        if resolved.attr1 is None:
            resolved.attr1 = getattr(global_settings, "attr1", "default1")
        if resolved.attr2 is None:
            resolved.attr2 = getattr(global_settings, "attr2", 10)
        return resolved


# --- _SettingsProxy Tests ---


def test_proxy_delegation(mocker: MockerFixture) -> None:
    model = MockGlobalSettings()
    on_update = mocker.stub()
    proxy = _SettingsProxy(model, on_update)

    assert proxy.attr1 == "default1"
    assert proxy.attr2 == 10
    on_update.assert_not_called()


def test_proxy_setattr(mocker: MockerFixture) -> None:
    model = MockGlobalSettings()
    on_update = mocker.stub()
    proxy = _SettingsProxy(model, on_update)

    proxy.attr1 = "new_value"
    on_update.assert_called_once_with("attr1", "new_value")


def test_proxy_repr(mocker: MockerFixture) -> None:
    model = MockGlobalSettings()
    proxy = _SettingsProxy(model, mocker.stub())
    assert repr(proxy) == repr(model)


def test_proxy_equality(mocker: MockerFixture) -> None:
    model1 = MockGlobalSettings(attr1="a")
    model2 = MockGlobalSettings(attr1="a")
    model3 = MockGlobalSettings(attr1="b")

    proxy1 = _SettingsProxy(model1, mocker.stub())
    proxy2 = _SettingsProxy(model2, mocker.stub())

    assert proxy1 == proxy2
    assert proxy1 == model1
    assert proxy1 != model3
    assert proxy1 != "not a proxy"


def test_proxy_slots(mocker: MockerFixture) -> None:
    model = MockGlobalSettings()
    proxy = _SettingsProxy(model, mocker.stub())

    # Verify __slots__ exist
    assert hasattr(proxy, "__slots__")
    assert "_model" in proxy.__slots__
    assert "_on_update" in proxy.__slots__

    # Attempting to set an arbitrary attribute should fail
    with pytest.raises(AttributeError, match=r"'MockGlobalSettings' object has no attribute 'unknown_attr'"):
        proxy.unknown_attr = 5


# --- _PluginSettingsStore Tests ---


@pytest.fixture
def mock_workspace(mocker: MockerFixture) -> MagicMock:
    return mocker.MagicMock()


@pytest.fixture
def mock_plugin(mocker: MockerFixture) -> MagicMock:
    plugin = mocker.MagicMock()
    plugin.identifier = "test_plugin"
    plugin.global_settings_model = MockGlobalSettings
    plugin.local_settings_model = MockLocalSettings
    return plugin


def test_store_get_caching(mock_workspace: MagicMock, mock_plugin: MagicMock) -> None:
    store = _PluginSettingsStore(mock_workspace)

    # Raw settings in isolated storage
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "from_storage"}

    # First call should fetch and validate
    settings1 = store.get(mock_plugin, "global")
    assert isinstance(settings1, MockGlobalSettings)
    assert settings1.attr1 == "from_storage"
    assert settings1.attr2 == 10

    # Modify storage - cache should still return old value
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "changed"}
    settings2 = store.get(mock_plugin, "global")
    assert settings2 is settings1
    assert isinstance(settings2, MockGlobalSettings)
    assert settings2.attr1 == "from_storage"


def test_store_local_global_resolution(mocker: MockerFixture, tmp_path: Path, mock_plugin: MagicMock) -> None:
    test_file = tmp_path / "test.vpy"
    mock_workspace = mocker.MagicMock(spec=BaseGenericFileWorkspace)
    mock_workspace.current_file_path = test_file
    store = _PluginSettingsStore(mock_workspace)

    # Setup global and local storage
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "global_val", "attr2": 100}
    SettingsManager.get_local_settings(test_file).plugins["test_plugin"] = {"attr2": 200}

    local_settings = store.get(mock_plugin, "local")
    assert isinstance(local_settings, MockLocalSettings)

    # attr1 should fall back to global, attr2 should be local
    assert local_settings.attr1 == "global_val"
    assert local_settings.attr2 == 200


def test_store_update_invalidates_cache(mock_workspace: MagicMock, mock_plugin: MagicMock) -> None:
    store = _PluginSettingsStore(mock_workspace)
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "old"}

    # Populate cache
    settings_old = store.get(mock_plugin, "global")
    assert isinstance(settings_old, MockGlobalSettings)
    assert settings_old.attr1 == "old"
    assert settings_old.attr2 == 10

    # Update through store
    store.update(mock_plugin, "global", attr1="new")

    # Verify persistence
    persisted = SettingsManager.global_settings.plugins["test_plugin"]
    assert persisted.attr1 == "new"  # pyright: ignore[reportAttributeAccessIssue]

    # Verify cache invalidation - next get should be fresh
    settings_new = store.get(mock_plugin, "global")
    assert settings_new is not settings_old
    assert isinstance(settings_new, MockGlobalSettings)
    assert settings_new.attr1 == "new"
    assert settings_new.attr2 == 10


def test_store_invalidate(mock_workspace: MagicMock, mock_plugin: MagicMock) -> None:
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "initial_val"}
    store = _PluginSettingsStore(mock_workspace)

    cached_settings = store.get(mock_plugin, "global")
    assert isinstance(cached_settings, MockGlobalSettings)
    assert cached_settings.attr1 == "initial_val"

    # Modify underlying storage
    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "updated_val"}

    # Without invalidation, get returns cached object
    assert store.get(mock_plugin, "global") is cached_settings

    # Invalidate global scope cache
    store.invalidate("global")

    # After invalidation, get returns a fresh instance with the updated value
    fresh_settings = store.get(mock_plugin, "global")
    assert fresh_settings is not cached_settings
    assert isinstance(fresh_settings, MockGlobalSettings)
    assert fresh_settings.attr1 == "updated_val"


# --- Integration Test ---


def test_plugin_settings_reactive_write(mock_workspace: MagicMock, mock_plugin: MagicMock) -> None:
    store = _PluginSettingsStore(mock_workspace)

    def get_proxy() -> _SettingsProxy[MockGlobalSettings]:
        model = cast(MockGlobalSettings, store.get(mock_plugin, "global"))
        return _SettingsProxy(model, lambda k, v: store.update(mock_plugin, "global", **{k: v}))

    SettingsManager.global_settings.plugins["test_plugin"] = {"attr1": "initial"}

    proxy = get_proxy()
    assert proxy.attr1 == "initial"

    # Reactive write
    proxy.attr1 = "updated"

    # Verify persistence
    persisted = SettingsManager.global_settings.plugins["test_plugin"]
    if hasattr(persisted, "attr1"):
        assert persisted.attr1 == "updated"  # pyright: ignore[reportAttributeAccessIssue]
    else:
        assert persisted["attr1"] == "updated"

    # Next read should be fresh due to cache invalidation in update()
    new_proxy = get_proxy()
    assert new_proxy.attr1 == "updated"
    assert new_proxy is not proxy  # Different model instance


def test_proxy_nested_model_update(mocker: MockerFixture) -> None:
    model = MockParentModel()
    on_update = mocker.stub()
    proxy = _SettingsProxy(model, on_update)

    proxy.nest.nest_attr = "new_nest_value"

    assert model.nest.nest_attr == "new_nest_value"
    on_update.assert_called_once()
    on_update.assert_called_once_with("nest", model.nest)


def test_proxy_deeply_nested_model_update(mocker: MockerFixture) -> None:
    model = MockLevel1()
    on_update = mocker.stub()
    proxy = _SettingsProxy(model, on_update)

    proxy.l2.l3.val = 42

    assert model.l2.l3.val == 42
    on_update.assert_called_once_with("l2", model.l2)
