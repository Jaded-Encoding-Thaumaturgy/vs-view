from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from functools import wraps
from inspect import iscoroutinefunction
from logging import getLogger
from threading import Lock, current_thread
from types import CoroutineType
from typing import Any, Literal, cast, overload

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication
from vsengine.loops import EventLoop, get_loop

type _Coro[R] = CoroutineType[Any, Any, R]

_logger = getLogger(__name__)


class QtEventLoop(QObject, EventLoop):
    """Qt event loop adapter for vsview."""

    _invoke = Signal(int)

    def attach(self) -> None:
        self._lock = Lock()
        self._counter = 0
        self._pending = dict[int, Callable[[], None]]()
        self._invoke.connect(self._on_invoke)

    def detach(self) -> None:
        self.wait_for_threads(100)
        self._invoke.disconnect(self._on_invoke)
        self._pending.clear()

    @Slot(int)
    def _on_invoke(self, task_id: int) -> None:
        with self._lock:
            wrapper = self._pending.pop(task_id, None)
        if wrapper:
            wrapper()

    def from_thread[**P, R](self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Future[R]:
        """Schedule func to run on the main Qt thread."""
        fut = Future[R]()

        def wrapper() -> None:
            if not fut.set_running_or_notify_cancel():
                return
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                _logger.debug(e, exc_info=True)
                fut.set_exception(e)
            else:
                fut.set_result(result)

        with self._lock:
            task_id = self._counter
            self._counter += 1
            self._pending[task_id] = wrapper

        self._invoke.emit(task_id)

        return fut

    def to_thread[**P, R](self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Future[R]:
        """Run func in Qt's global thread pool."""
        fut = Future[R]()

        def wrapper() -> None:
            if not fut.set_running_or_notify_cancel():
                return
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                _logger.error(e)
                _logger.debug(e, exc_info=True)
                fut.set_exception(e)
            else:
                fut.set_result(result)

        QThreadPool.globalInstance().start(QRunnable.create(wrapper))
        return fut

    def to_thread_named[**P, R](self, name: str, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> Future[R]:
        """Run func in Qt's global thread pool with a custom thread name."""
        fut = Future[R]()

        def wrapper() -> None:
            current_thread().name = name
            if not fut.set_running_or_notify_cancel():
                return
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                _logger.error(e)
                _logger.debug(e, exc_info=True)
                fut.set_exception(e)
            else:
                fut.set_result(result)

        QThreadPool.globalInstance().start(QRunnable.create(wrapper))
        return fut

    def wait_for_threads(self, timeout_ms: int = 500) -> None:
        """Wait for all background threads in the global thread pool to finish."""

        pool = QThreadPool.globalInstance()

        for _ in range(max(1, timeout_ms // 10)):
            if pool.activeThreadCount() == 0:
                break
            pool.waitForDone(10)
            QApplication.processEvents()

        # Final flush to process any signals from threads that just finished
        QApplication.processEvents()


@overload
def run_in_loop[**P, R](func: Callable[P, R] | Callable[P, _Coro[R]]) -> Callable[P, Future[R]]: ...


@overload
def run_in_loop[**P, R](
    *, return_future: Literal[False]
) -> Callable[[Callable[P, R] | Callable[P, _Coro[R]]], Callable[P, R]]: ...


def run_in_loop[**P, R](
    func: Callable[P, R] | Callable[P, _Coro[R]] | None = None, *, return_future: bool = True
) -> Any:
    """
    Decorator. Executes the decorated function within the QtEventLoop (Main Thread).

    Args:
        func: The function to wrap (when used as @run_in_loop without parens)
        return_future: If False, blocks and returns R directly.

    Returns: A future object or the result directly, depending on return_future.

    Usage:
        @run_in_loop
        def my_func(): ...

        @run_in_loop(return_future=False)
        def my_blocking_func(): ...
    """

    def decorator(fn: Callable[P, R] | Callable[P, _Coro[R]]) -> Callable[P, Future[R] | R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Future[R] | R:
            # Retrieve the global loop instance
            loop = get_loop()

            if iscoroutinefunction(fn):
                import asyncio

                coro = fn(*args, **kwargs)

                def run_coro() -> R:
                    try:
                        existing_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        return asyncio.run(coro)

                    return asyncio.run_coroutine_threadsafe(coro, existing_loop).result()

                fut = loop.from_thread(run_coro)
            else:
                # Delegate to from_thread to marshal execution to the main loop
                # We know fn is NOT a coroutine function here, so it returns R directly
                fut = loop.from_thread(cast(Callable[P, R], fn), *args, **kwargs)

            if return_future:
                return fut
            return fut.result()

        return wrapper

    if func is not None:
        return decorator(func)

    if return_future:
        raise NotImplementedError

    return decorator


@overload
def run_in_background[**P, R](func: Callable[P, R] | Callable[P, _Coro[R]]) -> Callable[P, Future[R]]: ...


@overload
def run_in_background[**P, R](
    *, name: str
) -> Callable[[Callable[P, R] | Callable[P, _Coro[R]]], Callable[P, Future[R]]]: ...


def run_in_background[**P, R](
    func: Callable[P, R] | Callable[P, _Coro[R]] | None = None, *, name: str | None = None
) -> Callable[P, Future[R]] | Callable[[Callable[P, R] | Callable[P, _Coro[R]]], Callable[P, Future[R]]]:
    """
    Executes the decorated function in a background thread (via QThreadPool) using the QtEventLoop's to_thread logic.

    Args:
        func: The function to wrap (when used as @run_in_background without parens)
        name: Optional thread name for logging (when used as @run_in_background(name="..."))

    Returns: A future object representing the result of the execution.

    Usage:
        @run_in_background
        def my_func(): ...

        @run_in_background(name="MyWorker")
        def my_named_func(): ...
    """

    def decorator(fn: Callable[P, R] | Callable[P, _Coro[R]]) -> Callable[P, Future[R]]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Future[R]:
            nonlocal fn

            loop = cast(QtEventLoop, get_loop())

            if iscoroutinefunction(fn):
                import asyncio

                coro = fn(*args, **kwargs)

                def run_coro() -> R:
                    try:
                        existing_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        return asyncio.run(coro)

                    return asyncio.run_coroutine_threadsafe(coro, existing_loop).result()

                if name is not None:
                    return loop.to_thread_named(name, run_coro)
                return loop.to_thread(run_coro)

            fn = cast(Callable[P, R], fn)

            if name is not None:
                return loop.to_thread_named(name, fn, *args, **kwargs)

            return loop.to_thread(fn, *args, **kwargs)

        return wrapper

    if func is not None:
        return decorator(func)

    return decorator
