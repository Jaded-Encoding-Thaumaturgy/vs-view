from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Coroutine, Generator
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from functools import wraps
from inspect import iscoroutinefunction
from logging import getLogger
from threading import Lock, current_thread
from typing import Any, Literal, Protocol, overload, override

from jetpytools import CustomTypeError
from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication
from vsengine.futures import UnifiedFuture
from vsengine.loops import Cancelled, EventLoop, get_loop

type _CoroutineFunc[**P, R] = Callable[P, Coroutine[Any, Any, R]]
type _Func[**P, R] = Callable[P, R]

_logger = getLogger(__name__)


class QtEventLoop(QObject, EventLoop):
    """Qt event loop adapter for vsview."""

    _invoke = Signal(int)

    @property
    def is_cancelled(self) -> bool:
        """Check if the event loop has been cancelled."""
        with self._lock:
            return self._cancelled

    @override
    def attach(self) -> None:
        self._lock = Lock()
        self._counter = 0
        self._tasks_lock = Lock()
        self._active_tasks = Counter[str]()
        self._pending = dict[int, Callable[[], None]]()
        self._cancelled = False
        self._invoke.connect(self._on_invoke)

    @override
    def detach(self) -> None:
        self.cancel()
        self.wait_for_threads(5000)
        self._invoke.disconnect(self._on_invoke)
        with self._lock:
            self._pending.clear()

    @Slot(int)
    def _on_invoke(self, task_id: int) -> None:
        with self._lock:
            wrapper = self._pending.pop(task_id, None)
        if wrapper:
            wrapper()

    @override
    def from_thread[**P, R](self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> UnifiedFuture[R]:
        """Schedule func to run on the main Qt thread."""
        fut = UnifiedFuture[R]()

        def wrapper() -> None:
            if not fut.set_running_or_notify_cancel():
                return
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                if isinstance(e, (Cancelled, CancelledError, asyncio.CancelledError)):
                    _logger.debug("Task cancelled: %s", e)
                else:
                    _logger.debug(e, exc_info=True)
                fut.set_exception(e)
            else:
                fut.set_result(result)

        if QThread.currentThread() == self.thread():
            wrapper()
            return fut

        with self._lock:
            task_id = self._counter
            self._counter += 1
            self._pending[task_id] = wrapper

        self._invoke.emit(task_id)

        return fut

    @override
    def to_thread[**P, R](self, func: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> UnifiedFuture[R]:
        """Run func in Qt's global thread pool."""
        return self.to_thread_named(func.__name__, func, *args, **kwargs)

    def to_thread_named[**P, R](
        self,
        name: str,
        func: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> UnifiedFuture[R]:
        """Run func in Qt's global thread pool with a custom thread name."""
        fut = UnifiedFuture[R]()

        def wrapper() -> None:
            with self._tasks_lock:
                self._active_tasks[name] += 1

            try:
                current_thread().name = name
                if not fut.set_running_or_notify_cancel():
                    return
                try:
                    result = func(*args, **kwargs)
                except BaseException as e:
                    if isinstance(e, (Cancelled, CancelledError, asyncio.CancelledError)):
                        _logger.debug("Task %r cancelled: %s", name, e)
                    else:
                        _logger.debug(e, exc_info=True)
                    fut.set_exception(e)
                else:
                    fut.set_result(result)
            finally:
                with self._tasks_lock:
                    self._active_tasks[name] -= 1

        QThreadPool.globalInstance().start(QRunnable.create(wrapper))
        return fut

    @override
    def next_cycle(self) -> UnifiedFuture[None]:
        """
        Pass control back to the event loop.

        If the loop is marked as cancelled, the returned future raises `Cancelled`.
        """
        fut = UnifiedFuture[None]()
        with self._lock:
            if self._cancelled:
                fut.set_exception(Cancelled())
                return fut

        self.from_thread(fut.set_result, None)
        return fut

    @override
    async def await_future[T](self, future: Future[T]) -> T:
        with self.wrap_cancelled():
            return await asyncio.wrap_future(future)

    @contextmanager
    @override
    def wrap_cancelled(self) -> Generator[None]:
        try:
            yield
        except (Cancelled, CancelledError, asyncio.CancelledError):
            raise Cancelled from None

    def cancel(self) -> None:
        """Mark the event loop as cancelled."""
        with self._lock:
            self._cancelled = True

    def reset_cancel(self) -> None:
        """Reset cancellation state."""
        with self._lock:
            self._cancelled = False

    def wait_for_threads(self, timeout_ms: int = 500) -> None:
        """
        Wait for background threads to finish.
        If called from a background thread, it ignores itself in the count.
        """
        _logger.debug("Calling wait_for_threads...")

        if not (app := QApplication.instance()):
            _logger.warning("No QApplication instance found")
            return

        is_main_thread = QThread.currentThread() == app.thread()
        current_name = current_thread().name

        with self._tasks_lock:
            target_count = int(self._active_tasks.get(current_name, 0) > 0)

        pool = QThreadPool.globalInstance()

        for _ in range(max(1, timeout_ms // 10)):
            count = pool.activeThreadCount()

            if count <= target_count:
                _logger.debug("Target thread count reached (%s), breaking loop.", count)
                break

            with self._tasks_lock:
                running = [k for k, v in self._active_tasks.items() if v > 0]

            _logger.debug("Active threads: %s (Target: %s)", count, target_count)
            _logger.debug("Running tasks: %r", running)

            _logger.debug("Waiting 10 ms...")
            pool.waitForDone(10)

            if is_main_thread:
                QApplication.processEvents()
        else:
            _logger.warning(
                "Timeout of %dms reached while waiting for threads. "  # No fmt
                "Proceeding while %d thread(s) are still active.",
                timeout_ms,
                pool.activeThreadCount(),
            )

        if is_main_thread:
            # Final flush to process any signals from threads that just finished
            QApplication.processEvents()

        with self._tasks_lock:
            _logger.debug("Final active threads count: %s", pool.activeThreadCount())
            _logger.debug("Remaining thread(s): %s", [k for k, v in self._active_tasks.items() if v > 0])


class _DecoratorFuture(Protocol):
    """Protocol for decorators that wrap a function and return a Future."""

    @overload
    def __call__[**P, R](self, func: _CoroutineFunc[P, R]) -> Callable[P, UnifiedFuture[R]]: ...
    @overload
    def __call__[**P, R](self, func: _Func[P, R]) -> Callable[P, UnifiedFuture[R]]: ...


class _DecoratorDirect(Protocol):
    """Protocol for decorators that wrap a function and return the result directly."""

    @overload
    def __call__[**P, R](self, func: _CoroutineFunc[P, R]) -> Callable[P, R]: ...
    @overload
    def __call__[**P, R](self, func: _Func[P, R]) -> Callable[P, R]: ...


@overload
def run_in_loop[**P, R](func: _CoroutineFunc[P, R]) -> Callable[P, UnifiedFuture[R]]: ...
@overload
def run_in_loop[**P, R](func: _Func[P, R]) -> Callable[P, UnifiedFuture[R]]: ...
@overload
def run_in_loop(*, return_future: Literal[True]) -> _DecoratorFuture: ...
@overload
def run_in_loop(*, return_future: Literal[False]) -> _DecoratorDirect: ...
@overload
def run_in_loop(*, return_future: bool) -> _DecoratorFuture | _DecoratorDirect: ...
def run_in_loop(func: Any = None, *, return_future: bool = True) -> Any:
    """
    Decorator. Executes the decorated function within the `QtEventLoop` (Main Thread).

    Args:
        func: The function to wrap (when used as `@run_in_loop` without parens)
        return_future: If False, blocks and returns R directly.

    Returns:
        A future object or the result directly, depending on return_future.

    Usage:
    ```python
    @run_in_loop
    def my_func(): ...


    @run_in_loop(return_future=False)
    def my_blocking_func(): ...
    ```
    """

    def decorator(fn: Any) -> Any:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not isinstance(loop := get_loop(), QtEventLoop):
                raise CustomTypeError("The current running loop isn't QtEventLoop")

            if iscoroutinefunction(fn):
                fut = loop.from_thread(_run_coro, fn(*args, **kwargs))
            else:
                # Delegate to from_thread to marshal execution to the main loop
                fut = loop.from_thread(fn, *args, **kwargs)

            return fut if return_future else fut.result()

        return wrapper

    return decorator if func is None else decorator(func)


@overload
def run_in_background[**P, R](func: _CoroutineFunc[P, R]) -> Callable[P, UnifiedFuture[R]]: ...
@overload
def run_in_background[**P, R](func: _Func[P, R]) -> Callable[P, UnifiedFuture[R]]: ...
@overload
def run_in_background(*, name: str) -> _DecoratorFuture: ...
def run_in_background(func: Any = None, *, name: str | None = None) -> Any:
    """
    Executes the decorated function in a background thread (via QThreadPool)
    using the `QtEventLoop`'s `to_thread` logic.

    Args:
        func: The function to wrap (when used as `@run_in_background` without parens)
        name: Optional thread name for logging (when used as `@run_in_background(name="...")`)

    Returns:
        A future object representing the result of the execution.

    Usage:
    ```python
    @run_in_background
    def my_func(): ...


    @run_in_background(name="MyWorker")
    def my_named_func(): ...
    ```
    """

    def decorator(fn: Any) -> Any:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not isinstance(loop := get_loop(), QtEventLoop):
                raise CustomTypeError("The current running loop isn't QtEventLoop")

            func_name = name or fn.__name__

            return (
                loop.to_thread_named(func_name, _run_coro, fn(*args, **kwargs))
                if iscoroutinefunction(fn)
                else loop.to_thread_named(func_name, fn, *args, **kwargs)
            )

        return wrapper

    return decorator if func is None else decorator(func)


def _run_coro[R](coro: Coroutine[Any, Any, R]) -> R:
    try:
        return asyncio.run(coro)
    except RuntimeError:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix=coro.__name__) as pool:
            return pool.submit(asyncio.run, coro).result()
