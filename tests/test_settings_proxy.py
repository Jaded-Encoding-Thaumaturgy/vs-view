from typing import Any, cast

import pytest
from pydantic import BaseModel
from pytest_mock import MockerFixture

from vsview.app.plugins._interface import _PluginSettingsStore, _SettingsProxy
from vsview.app.plugins.api import LocalSettingsModel


# Mock models for testing
class MockGlobalSettings(BaseModel):
    attr1: str = "default1"
    attr2: int = 10


class MockLocalSettings(LocalSettingsModel):
    attr1: str | None = None
    attr2: int | None = None

    def resolve(self, global_settings: BaseModel) -> "MockLocalSettings":
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
    with pytest.raises(AttributeError):
        proxy.unknown_attr = 5


# --- _PluginSettingsStore Tests ---


@pytest.fixture
def mock_workspace(mocker: MockerFixture) -> Any:
    return mocker.MagicMock()


@pytest.fixture
def mock_plugin(mocker: MockerFixture) -> Any:
    plugin = mocker.MagicMock()
    plugin.identifier = "test_plugin"
    plugin.global_settings_model = MockGlobalSettings
    plugin.local_settings_model = MockLocalSettings
    return plugin


def test_store_get_caching(mocker: MockerFixture, mock_workspace: Any, mock_plugin: Any) -> None:
    mock_settings_manager = mocker.patch("vsview.app.plugins._interface.SettingsManager")
    store = _PluginSettingsStore(mock_workspace)

    # Mock raw settings
    mock_settings_manager.global_settings.plugins = {"test_plugin": {"attr1": "from_storage"}}

    # First call should fetch and validate
    settings1 = store.get(mock_plugin, "global")
    assert settings1 is not None
    assert getattr(settings1, "attr1") == "from_storage"

    # Modify storage - cache should still return old value
    mock_settings_manager.global_settings.plugins["test_plugin"] = {"attr1": "changed"}
    settings2 = store.get(mock_plugin, "global")
    assert settings2 is settings1
    assert getattr(settings2, "attr1") == "from_storage"


def test_store_local_global_resolution(mocker: MockerFixture, mock_workspace: Any, mock_plugin: Any) -> None:
    mock_settings_manager = mocker.patch("vsview.app.plugins._interface.SettingsManager")
    store = _PluginSettingsStore(mock_workspace)

    # Setup global and local storage
    mock_settings_manager.global_settings.plugins = {"test_plugin": {"attr1": "global_val", "attr2": 100}}
    mock_settings_manager.get_local_settings.return_value.plugins = {"test_plugin": {"attr2": 200}}

    # Mock file_path for local settings
    mocker.patch.object(_PluginSettingsStore, "file_path", "test.vpy")

    local_settings = store.get(mock_plugin, "local")
    assert local_settings is not None

    # attr1 should fall back to global, attr2 should be local
    assert getattr(local_settings, "attr1") == "global_val"
    assert getattr(local_settings, "attr2") == 200


def test_store_update_invalidates_cache(mocker: MockerFixture, mock_workspace: Any, mock_plugin: Any) -> None:
    mock_settings_manager = mocker.patch("vsview.app.plugins._interface.SettingsManager")
    store = _PluginSettingsStore(mock_workspace)

    mock_settings_manager.global_settings.plugins = {"test_plugin": {"attr1": "old"}}

    # Populate cache
    settings_old = store.get(mock_plugin, "global")
    assert settings_old is not None
    assert getattr(settings_old, "attr1") == "old"

    # Update through store
    store.update(mock_plugin, "global", attr1="new")

    # Verify persistence
    persisted = mock_settings_manager.global_settings.plugins["test_plugin"]
    if hasattr(persisted, "attr1"):
        assert persisted.attr1 == "new"  # pyright: ignore[reportAttributeAccessIssue]
    else:
        assert persisted["attr1"] == "new"

    # Verify cache invalidation - next get should be fresh
    settings_new = store.get(mock_plugin, "global")
    assert settings_new is not settings_old
    assert settings_new is not None
    assert getattr(settings_new, "attr1") == "new"


def test_store_invalidate(mocker: MockerFixture, mock_workspace: Any, mock_plugin: Any) -> None:
    mock_settings_manager = mocker.patch("vsview.app.plugins._interface.SettingsManager")
    mock_settings_manager.global_settings.plugins = {"test_plugin": {"attr1": "val"}}
    store = _PluginSettingsStore(mock_workspace)

    store.get(mock_plugin, "global")
    # Accessing private cache for verification
    assert mock_plugin in store._caches["global"]

    store.invalidate("global")
    assert mock_plugin not in store._caches["global"]


# --- Integration Test ---


def test_plugin_settings_reactive_write(mocker: MockerFixture, mock_workspace: Any, mock_plugin: Any) -> None:
    mock_settings_manager = mocker.patch("vsview.app.plugins._interface.SettingsManager")
    store = _PluginSettingsStore(mock_workspace)

    def get_proxy() -> _SettingsProxy[MockGlobalSettings]:
        model = cast(MockGlobalSettings, store.get(mock_plugin, "global"))
        return _SettingsProxy(model, lambda k, v: store.update(mock_plugin, "global", **{k: v}))

    mock_settings_manager.global_settings.plugins = {"test_plugin": {"attr1": "initial"}}

    proxy = get_proxy()
    assert proxy.attr1 == "initial"

    # Reactive write
    proxy.attr1 = "updated"

    # Verify persistence call
    assert mock_settings_manager.global_settings.plugins["test_plugin"].attr1 == "updated"  # pyright: ignore[reportAttributeAccessIssue]

    # Next read should be fresh due to cache invalidation in update()
    new_proxy = get_proxy()
    assert new_proxy.attr1 == "updated"
    assert new_proxy is not proxy  # Different model instance
