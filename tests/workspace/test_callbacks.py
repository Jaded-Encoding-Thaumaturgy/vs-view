from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import shiboken6
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from vsview.app.workspace.callbacks import CallbackRegistry

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [pytest.param("a", "a", ["a"], id="same-arguments"), pytest.param("a", "b", ["a", "b"], id="other-arguments")],
)
def test_adding_the_same_callable_with_the_same_arguments_keeps_one_entry(
    first: str, second: str, expected: list[str]
) -> None:
    calls = list[str]()
    registry = CallbackRegistry()

    registry.add(calls.append, first)
    registry.add(calls.append, second)
    for cb in registry.take_all():
        cb()

    assert calls == expected


def test_arguments_that_cannot_be_hashed_are_kept_each_time() -> None:
    calls = list[list[int]]()
    registry = CallbackRegistry()

    registry.add(calls.append, [1])
    registry.add(calls.append, [1])
    for cb in registry.take_all():
        cb()

    assert calls == [[1], [1]]


def test_handle_remove_unregisters_the_callback() -> None:
    calls = list[str]()
    registry = CallbackRegistry()
    handle = registry.add(calls.append, "removed")
    registry.add(calls.append, "kept")

    handle.remove()
    handle.remove()
    for cb in registry.take_all():
        cb()

    assert calls == ["kept"]


def test_remove_unregisters_the_entry_with_the_same_callable_and_arguments() -> None:
    calls = list[str]()

    def record(name: str, *, suffix: str = "") -> None:
        calls.append(name + suffix)

    registry = CallbackRegistry()
    registry.add(record, "a", suffix="!")
    registry.add(record, "b")

    registry.remove(record, "a", suffix="!")
    for cb in registry.take_all():
        cb()

    assert calls == ["b"]


def test_snapshot_keeps_the_callbacks_and_take_all_removes_them() -> None:
    calls = list[str]()
    registry = CallbackRegistry()
    registry.add(calls.append, "a")
    registry.add(calls.append, "b")

    snapshot = registry.snapshot()
    taken = registry.take_all()
    left = registry.snapshot()

    assert len(snapshot) == 2
    for cb in taken:
        cb()
    assert calls == ["a", "b"]
    assert left == []


class _Owner(QObject):
    def cleanup(self) -> None: ...


@pytest.mark.qt
@pytest.mark.parametrize(
    "make_callback",
    [pytest.param(lambda: _Owner().cleanup, id="python-method"), pytest.param(lambda: QTimer().stop, id="qt-method")],
)
def test_a_method_of_a_destroyed_qobject_is_removed(
    qapp: QApplication, make_callback: Callable[[], Callable[[], Any]]
) -> None:
    registry = CallbackRegistry()
    cb = make_callback()
    registry.add(cb)
    owner = cb.__self__  # type: ignore[attr-defined]
    del cb

    shiboken6.delete(owner)

    assert registry.snapshot() == []
