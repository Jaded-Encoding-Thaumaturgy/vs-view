from __future__ import annotations

import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest
from PySide6.QtCore import QObject
from shiboken6 import Shiboken

from vsview.app.utils import QObjectCounter, QObjectSet

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def test_qobject_set_basic_operations() -> None:
    qset = QObjectSet[QObject]()
    obj1 = QObject()
    obj2 = QObject()

    assert len(qset) == 0
    assert bool(qset) is False
    assert obj1 not in qset

    qset.add(obj1)
    qset.add(obj1)  # Idempotent add
    qset.add(obj2)

    assert len(qset) == 2
    assert bool(qset) is True
    assert obj1 in qset
    assert obj2 in qset
    assert set(qset) == {obj1, obj2}

    qset.discard(obj1)
    qset.discard(obj1)  # Idempotent discard

    assert len(qset) == 1
    assert obj1 not in qset
    assert obj2 in qset

    qset.clear()

    assert len(qset) == 0
    assert bool(qset) is False
    assert list(qset) == []


def test_qobject_set_removes_on_cpp_destruction_with_live_python_wrapper() -> None:
    qset = QObjectSet[QObject]()
    parent = QObject()
    child = QObject(parent)
    qset.add(child)

    assert child in qset
    assert len(qset) == 1

    # Destroy the C++ object while holding a strong Python reference to `child`
    Shiboken.delete(parent)

    assert Shiboken.isValid(child) is False
    assert child not in qset
    assert len(qset) == 0
    assert bool(qset) is False
    assert list(qset) == []


def test_qobject_set_does_not_leak_strong_reference_on_python_gc() -> None:
    qset = QObjectSet[QObject]()
    obj: QObject | None = QObject()
    assert obj is not None
    ref = weakref.ref(obj)

    qset.add(obj)
    assert len(qset) == 1

    # Drop the only external strong reference
    obj = None
    gc.collect()

    assert ref() is None
    assert len(qset) == 0
    assert bool(qset) is False


def test_qobject_set_safe_mutation_during_iteration_and_concurrent_access() -> None:
    qset = QObjectSet[QObject]()
    objects = [QObject() for _ in range(20)]
    for obj in objects:
        qset.add(obj)

    visited = list[QObject]()
    for obj in qset:
        visited.append(obj)
        qset.discard(obj)

    assert len(visited) == 20
    assert len(qset) == 0

    # Concurrent reader/writer stress check
    pool = [QObject() for _ in range(50)]
    stop = threading.Event()

    def reader() -> int:
        iterations = 0
        while not stop.is_set():
            snapshot = list(qset)
            _ = len(qset)
            for item in snapshot:
                _ = item in qset
            iterations += 1
        return iterations

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(reader) for _ in range(3)]
        try:
            for _ in range(20):
                for obj in pool:
                    qset.add(obj)
                for obj in pool[:25]:
                    qset.discard(obj)
                qset.clear()
        finally:
            stop.set()

        for fut in futures:
            assert fut.result(timeout=5.0) >= 0


def test_qobject_counter_refcounting_and_cleanup() -> None:
    counter = QObjectCounter[QObject]()
    parent = QObject()
    child = QObject(parent)

    assert len(counter) == 0
    assert bool(counter) is False
    assert counter.count(child) == 0

    assert counter.add(child) == 1
    assert counter.add(child) == 2
    assert child in counter
    assert counter.count(child) == 2
    assert len(counter) == 1
    assert bool(counter) is True

    assert counter.discard(child) == 1
    assert counter.count(child) == 1
    assert child in counter

    # Re-increment then destroy in C++ while Python wrapper is still referenced
    assert counter.add(child) == 2
    Shiboken.delete(parent)

    assert Shiboken.isValid(child) is False
    assert child not in counter
    assert counter.count(child) == 0
    assert len(counter) == 0
    assert bool(counter) is False
