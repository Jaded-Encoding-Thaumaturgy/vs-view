from __future__ import annotations

import logging
import sys
import threading
import warnings
from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import suppress
from pathlib import Path
from types import CodeType, MethodType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import vapoursynth as vs
from jetpytools import SPath
from PySide6.QtCharts import QAbstractAxis, QAbstractSeries
from PySide6.QtCore import (
    QAbstractAnimation,
    QAbstractItemModel,
    QItemSelectionModel,
    QObject,
    QThread,
    QTimer,
    qInstallMessageHandler,
)
from PySide6.QtGui import QAction, QPixmap, QShortcut, QTextDocument, QWindow
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QButtonGroup,
    QCompleter,
    QGraphicsEffect,
    QGraphicsItem,
    QGraphicsScene,
    QLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot
from vsengine.loops import NO_LOOP, set_loop
from vsengine.policy import ManagedEnvironment

from tests.harness import HarnessPluginController, MockQObject
from vsview.app.outputs.audio import AudioOutput
from vsview.app.outputs.video import VideoOutput
from vsview.app.packing import Packer
from vsview.app.plugins import specs
from vsview.app.plugins.manager import PluginManager
from vsview.app.settings.manager import SettingsManager, SettingsSignals
from vsview.app.settings.models import GlobalSettings
from vsview.app.settings.secrets import SecretsManager
from vsview.app.tools.frameprops.categories import CategoryRegistry
from vsview.app.tools.frameprops.formatters import FormatterRegistry
from vsview.app.utils import path_to_hash
from vsview.vsenv.loop import QtEventLoop


class QtThreadAffinityWarning(RuntimeWarning):
    """Warning emitted when a Qt UI object is accessed from a non-affinity background thread."""


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


# Logging Isolation
@pytest.fixture(autouse=True)
def isolate_logging() -> Generator[None]:
    """Isolate logging configuration so tests that call setup_logging don't pollute subsequent tests."""
    root_logger = logging.getLogger()
    orig_handlers = root_logger.handlers[:]
    orig_level = root_logger.level

    try:
        yield
    finally:
        for h in root_logger.handlers[:]:
            if h not in orig_handlers:
                root_logger.removeHandler(h)
                with suppress(Exception):
                    h.close()

        root_logger.handlers = orig_handlers[:]
        root_logger.setLevel(orig_level)
        qInstallMessageHandler(None)
        logging.captureWarnings(False)


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
    if SettingsManager in type(SettingsManager)._instances:
        sm = SettingsManager()
        sm._global_settings = sm.default_global_settings
        sm._local_settings.clear()
        sm._signals = SettingsSignals(QApplication.instance())


@pytest.fixture
def fresh_settings_manager() -> Generator[Callable[[], SettingsManager], None, None]:
    """Factory fixture to safely re-instantiate SettingsManager singleton with guaranteed teardown."""
    original: SettingsManager | None = type(SettingsManager)._instances.get(SettingsManager)

    def reinit() -> SettingsManager:
        type(SettingsManager)._instances.pop(SettingsManager, None)
        return SettingsManager()

    try:
        yield reinit
    finally:
        # Restore the original singleton so existing signal connections stay valid
        type(SettingsManager)._instances.pop(SettingsManager, None)

        if original is not None:
            type(SettingsManager)._instances[SettingsManager] = original
            original._global_settings = original.default_global_settings
            original._local_settings.clear()
            original._signals = SettingsSignals(QApplication.instance())


# Qt Event Loop & Lifecycle
@pytest.fixture(autouse=True)
def warn_qt_thread_affinity() -> Generator[None]:
    """
    Warn whenever a Qt UI method or constructor is called from a background thread.

    - PEP 669: https://peps.python.org/pep-0669/
    - Python docs: https://docs.python.org/3/library/sys.monitoring.html
    """

    # Top-level Qt base classes and widgets that must only live on the GUI thread.
    # Subclasses are covered via issubclass/isinstance.
    ui_types = (
        QAbstractAxis,
        QAbstractSeries,
        QWebEnginePage,
        QWidget,
        QGraphicsItem,
        QGraphicsScene,
        QLayout,
        QAbstractItemModel,
        QItemSelectionModel,
        QAction,
        QShortcut,
        QTimer,
        QPixmap,
        QWindow,
        QAbstractAnimation,
        QAbstractItemDelegate,
        QGraphicsEffect,
        QTextDocument,
        QButtonGroup,
        QCompleter,
    )
    repo_root = str(Path(__file__).resolve().parent.parent)
    loop_file = QtEventLoop.to_thread_scoped.__code__.co_filename
    main_thread = threading.main_thread()

    # PEP 669 tool ID allocation:
    # https://docs.python.org/3/library/sys.monitoring.html#sys.monitoring.get_tool
    # Dynamically select an available slot (3 or 4) to prevent conflicts.
    tool_id = next(i for i in (3, 4) if sys.monitoring.get_tool(i) is None)

    # Qt C++ methods documented as thread-safe to call from any thread.
    safe_cross_thread_methods = {"deleteLater"}

    def on_call(code: CodeType, offset: int, call_obj: object, arg0: object) -> object | None:
        # Exclude .venv, third-party libraries, stdlib, and loop marshal code
        if (
            not code.co_filename.startswith(repo_root)
            or "\\.venv\\" in code.co_filename
            or "/.venv/" in code.co_filename
            or code.co_filename == loop_file
        ):
            return sys.monitoring.DISABLE

        # Running on main Python thread with an active QApplication.
        if (app := QApplication.instance()) is None or threading.current_thread() is main_thread:
            return None

        # Running on Qt's main GUI QThread (handles foreign threads wrapped by Qt).
        if (current_qt_thread := QThread.currentThread()) == app.thread():
            return None

        # Ignore known thread-safe Qt methods.
        if (func_name := getattr(call_obj, "__name__", "")) in safe_cross_thread_methods:
            return None

        # Bound method call on an existing UI instance.
        # In PySide6, bound C++ Qt methods have __self__ set to the C++ instance
        # and their type is builtin_function_or_method (NOT Python MethodType).
        # By excluding MethodType, pure-Python helper methods on QWidget subclasses (e.g. self.loader())
        # do not trigger false positives.
        # Warnings only fire when code actually invokes a C++ Qt method.
        target = getattr(call_obj, "__self__", None)
        if isinstance(target, ui_types) and not isinstance(call_obj, MethodType):
            expected_thread = target.thread() if isinstance(target, QObject) else app.thread()
            if current_qt_thread != expected_thread:
                lineno = next(
                    (line for start, end, line in code.co_lines() if start <= offset < end),
                    code.co_firstlineno,
                )
                name = func_name or repr(call_obj)
                warnings.warn_explicit(
                    f"Qt UI thread affinity violation: {type(target).__name__}.{name}() "
                    f"called from background thread {threading.current_thread().name!r}",
                    QtThreadAffinityWarning,
                    code.co_filename,
                    lineno or -1,
                )
        # Instantiation of a UI class (QPixmap() or MyWidget() on a background thread).
        elif isinstance(call_obj, type) and issubclass(call_obj, ui_types):
            lineno = next(
                (line for start, end, line in code.co_lines() if start <= offset < end),
                code.co_firstlineno,
            )
            warnings.warn_explicit(
                f"Qt UI thread affinity violation: {call_obj.__name__}() instantiated "
                f"from background thread {threading.current_thread().name!r}",
                QtThreadAffinityWarning,
                code.co_filename,
                lineno or -1,
            )

        return None

    sys.monitoring.use_tool_id(tool_id, "qt_thread_guard")
    sys.monitoring.register_callback(tool_id, sys.monitoring.events.CALL, on_call)
    sys.monitoring.set_events(tool_id, sys.monitoring.events.CALL)
    try:
        yield
    finally:
        # Guarantee full cleanup so test state never leaks into other runs.
        sys.monitoring.set_events(tool_id, 0)
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.CALL, None)
        sys.monitoring.free_tool_id(tool_id)


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
def mock_qobject() -> MockQObject:
    """Dynamic MockQObject adapter satisfying PySide6 C++ QObject checks."""
    return MockQObject()


@pytest.fixture
def mock_qobject_factory() -> Callable[..., MockQObject]:
    """Factory creating dynamic MockQObject adapters."""
    return MockQObject


@pytest.fixture
def plugin_controller(qtbot: QtBot, tmp_path: Path) -> HarnessPluginController:
    return HarnessPluginController(qtbot, tmp_path)


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


# Category Registry Isolation
@pytest.fixture
def isolate_category_registry() -> Generator[None]:
    """Isolate CategoryRegistry singleton between tests."""
    registry = CategoryRegistry()
    orig_matchers = registry._matchers.copy()
    try:
        registry._matchers.clear()
        yield
    finally:
        registry._matchers.clear()
        registry._matchers.extend(orig_matchers)
