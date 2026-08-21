from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import CancelledError as ConcurrentCancelledError

import pytest
from PySide6.QtCore import QThread, QThreadPool
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from vsengine.futures import UnifiedFuture
from vsengine.loops import Cancelled, set_loop

from vsview.vsenv.loop import QtEventLoop, run_in_background, run_in_loop


@pytest.fixture
def qt_loop(qapp: QApplication) -> QtEventLoop:
    loop = QtEventLoop(qapp)
    set_loop(loop)
    return loop


def test_qteventloop_lifecycle(qapp: QApplication) -> None:
    loop = QtEventLoop(qapp)
    loop.attach()

    assert loop.is_cancelled is False

    loop.cancel()
    assert loop.is_cancelled is True

    loop.reset_cancel()  # type: ignore[unreachable]
    assert loop.is_cancelled is False

    loop.detach()


def test_from_thread_execution_main_and_bg(qt_loop: QtEventLoop, qapp: QApplication, qtbot: QtBot) -> None:
    # Schedule from main thread
    executed_thread: QThread | None = None

    def main_func(val: int) -> int:
        nonlocal executed_thread
        executed_thread = QThread.currentThread()
        return val * 2

    fut1 = qt_loop.from_thread(main_func, 21)
    qtbot.waitUntil(lambda: fut1.done(), timeout=2000)
    assert fut1.result() == 42
    assert executed_thread == qapp.thread()

    # Schedule from background thread
    bg_result: int | None = None

    def bg_worker() -> None:
        nonlocal bg_result
        fut2 = qt_loop.from_thread(main_func, 50)
        bg_result = fut2.result()

    t = threading.Thread(target=bg_worker)
    t.start()
    qtbot.waitUntil(lambda: bg_result is not None, timeout=2000)
    t.join()
    assert bg_result == 100


def test_from_thread_exception_handling(qt_loop: QtEventLoop, qtbot: QtBot) -> None:
    def error_func() -> None:
        raise ValueError("standard error")

    fut1 = qt_loop.from_thread(error_func)
    qtbot.waitUntil(lambda: fut1.done(), timeout=2000)
    assert isinstance(fut1.exception(), ValueError)
    with pytest.raises(ValueError, match="standard error"):
        fut1.result()

    # Cancelled exception (vsengine Cancelled)
    def cancel_func() -> None:
        raise Cancelled("cancelled task")

    fut2 = qt_loop.from_thread(cancel_func)
    qtbot.waitUntil(lambda: fut2.done(), timeout=2000)
    assert isinstance(fut2.exception(), Cancelled)

    # asyncio CancelledError
    def asyncio_cancel_func() -> None:
        raise asyncio.CancelledError("asyncio cancelled")

    fut3 = qt_loop.from_thread(asyncio_cancel_func)
    qtbot.waitUntil(lambda: fut3.done(), timeout=2000)
    assert isinstance(fut3.exception(), asyncio.CancelledError)


def test_to_thread_execution_and_naming(qt_loop: QtEventLoop) -> None:
    # Default name via to_thread
    default_thread_name = ""

    def default_named_func(a: int, b: int) -> int:
        nonlocal default_thread_name
        default_thread_name = threading.current_thread().name
        return a + b

    fut1 = qt_loop.to_thread(default_named_func, 10, 20)
    assert fut1.result(timeout=2.0) == 30
    assert default_thread_name == "default_named_func"

    # Custom name via to_thread_named
    named_func_name = ""

    def named_func() -> str:
        nonlocal named_func_name
        named_func_name = threading.current_thread().name
        return "ok"

    fut2 = qt_loop.to_thread_named("CustomWorkerThread", named_func)
    assert fut2.result(timeout=2.0) == "ok"
    assert named_func_name == "CustomWorkerThread"


def test_to_thread_exception_handling(qt_loop: QtEventLoop) -> None:
    def fail_func() -> None:
        raise RuntimeError("bg thread failure")

    fut1 = qt_loop.to_thread(fail_func)
    with pytest.raises(RuntimeError, match="bg thread failure"):
        fut1.result(timeout=2.0)

    def cancel_func() -> None:
        raise Cancelled("bg task cancelled")

    fut2 = qt_loop.to_thread(cancel_func)
    assert isinstance(fut2.exception(timeout=2.0), Cancelled)

    def concurrent_cancel_func() -> None:
        raise ConcurrentCancelledError("bg task concurrent cancelled")

    fut3 = qt_loop.to_thread_named("ConcurrentCancelWorker", concurrent_cancel_func)
    assert isinstance(fut3.exception(timeout=2.0), ConcurrentCancelledError)


def test_from_thread_pre_cancelled_future(qt_loop: QtEventLoop, qtbot: QtBot) -> None:
    called = False

    def inner_task() -> None:
        nonlocal called
        called = True

    def bg_worker() -> None:
        fut1 = qt_loop.from_thread(inner_task)
        assert fut1.cancel()

    t = threading.Thread(target=bg_worker)
    t.start()
    t.join()

    qtbot.wait(100)
    assert called is False


def test_to_thread_pre_cancelled_future(qt_loop: QtEventLoop, qtbot: QtBot) -> None:
    called = False

    def task() -> None:
        nonlocal called
        called = True

    fut_real = qt_loop.to_thread(task)
    assert fut_real.cancel()
    qtbot.wait(100)
    assert called is False


def test_next_cycle(qt_loop: QtEventLoop, qtbot: QtBot) -> None:
    # Normal cycle
    fut1 = qt_loop.next_cycle()
    qtbot.waitUntil(lambda: fut1.done(), timeout=2000)
    assert fut1.result() is None

    # Cancelled cycle
    qt_loop.cancel()
    fut2 = qt_loop.next_cycle()
    assert isinstance(fut2.exception(), Cancelled)


def test_wait_for_threads_main_thread(qt_loop: QtEventLoop) -> None:
    start_evt = threading.Event()
    finish_evt = threading.Event()

    def long_task() -> None:
        start_evt.set()
        finish_evt.wait(0.5)

    qt_loop.to_thread_named("LongTaskThread", long_task)
    assert start_evt.wait(2.0), "Task did not start in time"
    assert QThreadPool.globalInstance().activeThreadCount() > 0

    finish_evt.set()
    qt_loop.wait_for_threads(timeout_ms=1000)
    assert QThreadPool.globalInstance().activeThreadCount() == 0


def test_wait_for_threads_from_background_thread(qt_loop: QtEventLoop) -> None:
    called = False

    def bg_task_calling_wait() -> bool:
        nonlocal called
        qt_loop.wait_for_threads(timeout_ms=500)
        called = True
        return True

    fut = qt_loop.to_thread_named("SelfWaitWorker", bg_task_calling_wait)
    assert fut.result(timeout=2.0) is True
    assert called is True


def test_wait_for_threads_no_qapp(
    qt_loop: QtEventLoop,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(QApplication, "instance", staticmethod(lambda: None))

    # Should log warning and return without error
    with caplog.at_level(logging.WARNING):
        qt_loop.wait_for_threads(timeout_ms=100)

    assert "No QApplication instance found" in caplog.text


def test_wait_for_threads_timeout(qt_loop: QtEventLoop, caplog: pytest.LogCaptureFixture) -> None:
    start_evt = threading.Event()
    block_evt = threading.Event()

    def blocking_task() -> None:
        start_evt.set()
        block_evt.wait(2.0)

    try:
        qt_loop.to_thread_named("BlockingTask", blocking_task)
        assert start_evt.wait(2.0), "Task did not start in time"

        with caplog.at_level(logging.WARNING):
            qt_loop.wait_for_threads(timeout_ms=50)

        assert "Timeout of 50ms reached" in caplog.text
    finally:
        block_evt.set()
        qt_loop.wait_for_threads(timeout_ms=1000)


def test_run_in_loop_decorator(qt_loop: QtEventLoop, qtbot: QtBot) -> None:
    # Sync function with return_future=True (default)
    @run_in_loop
    def add_sync(a: int, b: int) -> int:
        return a + b

    fut1 = add_sync(3, 4)
    assert isinstance(fut1, UnifiedFuture)
    qtbot.waitUntil(lambda: fut1.done(), timeout=2000)
    assert fut1.result() == 7

    # Sync function with return_future=False
    @run_in_loop(return_future=False)
    def add_sync_direct(a: int, b: int) -> int:
        return a + b

    assert add_sync_direct(10, 20) == 30

    # Async function with return_future=True
    @run_in_loop
    async def add_async(a: int, b: int) -> int:
        await asyncio.sleep(0.01)
        return a + b

    fut2 = add_async(5, 6)
    assert isinstance(fut2, UnifiedFuture)
    qtbot.waitUntil(lambda: fut2.done(), timeout=2000)
    assert fut2.result() == 11

    # Async function with return_future=False
    @run_in_loop(return_future=False)
    async def add_async_direct(a: int, b: int) -> int:
        await asyncio.sleep(0.01)
        return a + b

    assert add_async_direct(100, 200) == 300


def test_run_in_background_decorator(qt_loop: QtEventLoop) -> None:
    # Sync function default
    @run_in_background
    def sync_bg(x: int) -> int:
        return x * 3

    fut1 = sync_bg(10)
    assert fut1.result(timeout=2.0) == 30

    # Sync function with custom thread name
    thread_name = ""

    @run_in_background(name="NamedBgWorker")
    def sync_named_bg() -> str:
        nonlocal thread_name
        thread_name = threading.current_thread().name
        return "bg_done"

    fut2 = sync_named_bg()
    assert fut2.result(timeout=2.0) == "bg_done"
    assert thread_name == "NamedBgWorker"

    # Async function in background
    @run_in_background
    async def async_bg(val: str) -> str:
        await asyncio.sleep(0.01)
        return f"hello {val}"

    fut3 = async_bg("world")
    assert fut3.result(timeout=2.0) == "hello world"


def test_run_coro_existing_event_loop(qt_loop: QtEventLoop) -> None:
    main_thread = threading.current_thread()

    @run_in_loop
    async def coro1() -> str:
        @run_in_loop
        async def innercoro() -> str:
            assert threading.current_thread() == main_thread
            return "simple"

        return await innercoro()

    assert coro1().result(timeout=2.0) == "simple"


def test_run_coro_existing_event_loop_exception(qt_loop: QtEventLoop) -> None:
    main_thread = threading.current_thread()

    @run_in_loop
    async def coro1() -> str:
        @run_in_loop
        async def innercoro() -> str:
            assert threading.current_thread() == main_thread
            raise ValueError("inner failure")

        return await innercoro()

    fut = coro1()
    with pytest.raises(ValueError, match="inner failure"):
        fut.result(timeout=2.0)

    @run_in_loop
    async def coro2() -> str:
        @run_in_loop
        async def innercoro() -> str:
            assert threading.current_thread() == main_thread
            raise asyncio.CancelledError

        return await innercoro()

    fut = coro2()
    with pytest.raises(Cancelled):
        fut.result(timeout=2.0)


def test_run_coro_existing_event_loop_nested(qt_loop: QtEventLoop) -> None:
    main_thread = threading.current_thread()

    @run_in_loop
    async def level1() -> str:
        assert threading.current_thread() == main_thread

        @run_in_loop
        async def level2() -> str:
            assert threading.current_thread() == main_thread

            @run_in_loop
            async def level3() -> str:
                assert threading.current_thread() == main_thread
                return "deep"

            return await level3()

        return await level2()

    assert level1().result(timeout=2.0) == "deep"
