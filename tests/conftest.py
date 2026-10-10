from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import vapoursynth as vs
from jetpytools import SPath
from jetpytools.types.utils import _self_objects_cache
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from vsengine.loops import NO_LOOP, set_loop
from vsengine.policy import ManagedEnvironment

from tests.harness import PluginTestHarness
from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins import specs
from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.manager import SettingsManager, SettingsSignals
from vsview.app.settings.models import GlobalSettings
from vsview.app.settings.secrets import SecretsManager
from vsview.app.tools.frameprops.formatters import FormatterRegistry
from vsview.app.utils import path_to_hash
from vsview.vsenv.loop import QtEventLoop


class _KeyringError(Exception):
    pass


class _PasswordDeleteError(_KeyringError):
    pass


class InMemoryKeyring:
    """In-memory keyring fake that prevents test pollution of the host OS credential store."""

    errors = SimpleNamespace(KeyringError=_KeyringError, PasswordDeleteError=_PasswordDeleteError)

    def __init__(self) -> None:
        self._storage = dict[tuple[str, str], str]()

    def get_password(self, service: str, username: str) -> str | None:
        return self._storage.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._storage[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) in self._storage:
            del self._storage[(service, username)]
        else:
            raise self.errors.PasswordDeleteError(f"No password found for {service}:{username}")

    def get_credential(self, service: str, username: str | None = None) -> Any | None:
        for (svc, user), pwd in self._storage.items():
            if svc == service and (username is None or user == username):
                cred = MagicMock()
                cred.username = user
                cred.password = pwd
                return cred
        return None


# Secrets Manager Isolation
@pytest.fixture(autouse=True)
def isolate_secrets(monkeypatch: pytest.MonkeyPatch) -> InMemoryKeyring:
    """Isolate SecretsManager to an in-memory storage dictionary for all tests."""
    fake_keyring = InMemoryKeyring()
    monkeypatch.setattr(
        SecretsManager,
        "keyring",
        property(lambda self: fake_keyring),
    )
    monkeypatch.setattr(
        SecretsManager,
        "KeyringError",
        property(lambda self: fake_keyring.errors.KeyringError),
    )
    monkeypatch.setattr(
        SecretsManager,
        "PasswordDeleteError",
        property(lambda self: fake_keyring.errors.PasswordDeleteError),
    )
    return fake_keyring


# Global Settings Isolation
@pytest.fixture(autouse=True)
def isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    # Redirect global and local settings paths to isolated pytest tmp_path
    fake_dir = SPath(tmp_path / "vsview_config")
    fake_config = fake_dir / "global_settings.json"
    monkeypatch.setattr(GlobalSettings, "config_path", fake_dir)
    monkeypatch.setattr(GlobalSettings, "path", fake_config)
    monkeypatch.setattr(GlobalSettings, "path_env", fake_config)
    monkeypatch.setattr(
        SettingsManager,
        "local_settings_path",
        staticmethod(lambda p: tmp_path / ".vsjet" / "vsview" / f"{path_to_hash(p)}.json"),
    )

    # Reset settings on existing instance and clean up accumulated signal connections
    _reset_settings_manager()

    try:
        yield
    finally:
        _reset_settings_manager()


def _reset_settings_manager() -> None:
    _self_objects_cache.pop(SettingsManager, None)
    if SettingsManager in type(SettingsManager)._instances:
        sm = SettingsManager()
        sm._global_settings = sm.default_global_settings
        sm._local_settings.clear()
        sm._signals = SettingsSignals(QApplication.instance())
        _self_objects_cache[SettingsManager] = sm


@pytest.fixture
def fresh_settings_manager() -> Generator[Callable[[], SettingsManager], None, None]:
    """Factory fixture to safely re-instantiate SettingsManager singleton with guaranteed teardown."""
    original: SettingsManager | None = type(SettingsManager)._instances.get(SettingsManager)
    original_cache = _self_objects_cache.get(SettingsManager)

    def reinit() -> SettingsManager:
        type(SettingsManager)._instances.pop(SettingsManager, None)
        _self_objects_cache.pop(SettingsManager, None)
        sm = SettingsManager()
        _self_objects_cache[SettingsManager] = sm
        return sm

    try:
        yield reinit
    finally:
        # Restore the original singleton so existing signal connections stay valid
        type(SettingsManager)._instances.pop(SettingsManager, None)
        _self_objects_cache.pop(SettingsManager, None)

        if original is not None:
            type(SettingsManager)._instances[SettingsManager] = original
            original._global_settings = original.default_global_settings
            original._local_settings.clear()
            original._signals = SettingsSignals(QApplication.instance())
            _self_objects_cache[SettingsManager] = original
        elif original_cache is not None:
            _self_objects_cache[SettingsManager] = original_cache


# Qt Event Loop & Lifecycle
@pytest.fixture
def qt_event_loop(qapp: QApplication) -> Generator[QtEventLoop, None, None]:
    """Provide a QtEventLoop bound to qapp and set in vsengine, resetting on teardown."""
    loop = QtEventLoop(qapp)
    set_loop(loop)
    try:
        yield loop
    finally:
        set_loop(NO_LOOP)


# Plugin Manager & Hookspecs
@pytest.fixture
def init_plugin_manager() -> PluginManager:
    """Initialize PluginManager entrypoints and base hookspecs for tests."""
    pm = PluginManager()
    pm._entry_points_loaded = True
    if not hasattr(pm.manager.hook, "vsview_get_video_processor"):
        pm.manager.add_hookspecs(specs)
    if pm._load_future is None:
        f = Future[None]()
        f.set_result(None)
        pm._load_future = f
    return pm


@pytest.fixture
def mock_api() -> MagicMock:
    """Mock PluginAPI instance with common default attributes."""
    api = MagicMock()
    api.busy = False
    api.blocker.return_value.__enter__.return_value = None
    api.vs_context.return_value.__enter__.return_value = None
    return api


@pytest.fixture
def plugin_harness(qtbot: QtBot, tmp_path: Path) -> PluginTestHarness:
    """Formalized test harness satisfying PluginAPI protocol with real signals and settings."""
    return PluginTestHarness(qtbot, tmp_path)


# VapourSynth & Environment Fixtures


@pytest.fixture
def sample_video_clip() -> vs.VideoNode:
    """Standard 64x64 10-frame RGB24 BlankClip for video output tests."""
    return vs.core.std.BlankClip(format=vs.RGB24, width=64, height=64, length=10)


@pytest.fixture
def sample_audio_node() -> vs.AudioNode:
    """Standard 10000-sample BlankAudio for audio output tests."""
    return vs.core.std.BlankAudio(length=10000)


@pytest.fixture
def create_test_video_output() -> Callable[[ManagedEnvironment, int], VideoOutput]:
    """Factory fixture to create a valid packed VideoOutput with bt709 properties."""

    def create(env: ManagedEnvironment, num_frames: int = 100) -> VideoOutput:
        with env.use():
            clip = env.core.std.BlankClip(
                length=num_frames,
                width=64,
                height=64,
                format=vs.RGB24,
                fpsnum=24,
                fpsden=1,
            )
            clip = env.core.std.SetFrameProps(
                clip,
                _Matrix=vs.MATRIX_RGB,
                _Primaries=vs.PRIMARIES_BT709,
                _Transfer=vs.TRANSFER_BT709,
            )
            packer = Packer()
            voutput = VideoOutput(vs.VideoOutputTuple(clip, None, 0), 0, packer)
            voutput.prepared_clip = packer.pack_clip(clip, None)
            return voutput

    return create


@pytest.fixture
def create_test_audio_output() -> Callable[[ManagedEnvironment, int], AudioOutput]:
    """Factory fixture to create a valid AudioOutput."""

    def create(env: ManagedEnvironment, num_frames: int = 100) -> AudioOutput:
        with env.use():
            clip = env.core.std.BlankAudio(length=num_frames)
            voutput = AudioOutput(clip, 0)
            voutput.prepared_audio = clip
            return voutput

    return create


@pytest.fixture
def fixture_video_file() -> Path:
    """Path to the real video fixture tests/fixtures/鬼滅のRICKROLL.mp4."""
    video_path = Path(__file__).parent / "fixtures" / "鬼滅のRICKROLL.mp4"
    if not video_path.is_file():
        pytest.skip(f"Fixture video not found: {video_path}")
    return video_path


# Formatter Registry Isolation
@pytest.fixture
def isolate_formatter_registry() -> Generator[None]:
    """Isolate FormatterRegistry singleton between tests."""
    registry = FormatterRegistry()
    orig_formatters = registry._formatters.copy()
    orig_order = registry._order.copy()
    orig_next_order = registry._next_order
    try:
        registry._formatters.clear()
        registry._order.clear()
        registry._next_order = 0
        yield
    finally:
        registry._formatters.clear()
        registry._formatters.update(orig_formatters)
        registry._order.clear()
        registry._order.update(orig_order)
        registry._next_order = orig_next_order
