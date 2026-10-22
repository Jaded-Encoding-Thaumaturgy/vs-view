from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable, Coroutine, Generator, Sequence
from concurrent.futures import CancelledError, Future, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from inspect import iscoroutinefunction
from logging import DEBUG, getLogger
from typing import Any, Literal, NamedTuple, Protocol, overload, override

from jetpytools import CustomTypeError, clamp
from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication
from vsengine.futures import UnifiedFuture
from vsengine.loops import Cancelled, EventLoop, get_loop

type _CoroutineFunc[**P, R] = Callable[P, Coroutine[Any, Any, R]]
type _Func[**P, R] = Callable[P, R]

_logger = getLogger(__name__)
_current_task_future = ContextVar[UnifiedFuture[Any] | None]("_current_task_future", default=None)


class TaskInfo(NamedTuple):
    future: UnifiedFuture[Any]
    name: str
    owner: object


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
        self._lock = threading.Lock()
        self._counter = 0
        self._tasks_lock = threading.Lock()
        self._active_tasks = Counter[str]()
        self._tracked_tasks = dict[UnifiedFuture[Any], TaskInfo]()
        self._thread_futures = dict[int, UnifiedFuture[Any]]()
        self._pending = dict[int, Callable[[], None]]()
        self._cancelled = False
        self._invoke.connect(self._on_invoke)

    @override
    def detach(self) -> None:
        from ..app.main import Application

        if isinstance(QApplication.instance(), Application):
            _logger.warning("Attempt to detach QtEventLoop within VSView context")

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
        return self.to_thread_scoped(None, name, func, *args, **kwargs)

    def to_thread_scoped[**P, R](
        self,
        owner: object,
        name: str,
        func: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> UnifiedFuture[R]:
        """Run func in Qt's global thread pool with a custom thread name and owner for scoped waits."""
        fut = UnifiedFuture[R]()
        info = TaskInfo(fut, name, owner)

        with self._tasks_lock:
            self._active_tasks[name] += 1
            self._tracked_tasks[fut] = info

        ctx = copy_context()

        def wrapper() -> None:
            thread_id = threading.get_ident()
            with self._tasks_lock:
                self._thread_futures[thread_id] = fut

            try:
                threading.current_thread().name = name
                if not fut.set_running_or_notify_cancel():
                    return

                def fn_wrapped() -> R:
                    _current_task_future.set(fut)
                    return func(*args, **kwargs)

                try:
                    result = ctx.run(fn_wrapped)
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
                    self._thread_futures.pop(thread_id, None)
                    self._active_tasks[name] -= 1
                    self._tracked_tasks.pop(fut, None)

        _logger.log(DEBUG - 1, "QtEventLoop: Running task %r; owner %r; func %r", info.name, info.owner, func)
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

    def wait_for_threads(self, timeout_ms: int = 500, *, owner: Any = None, names: Sequence[str] | None = None) -> None:
        """
        Wait for background threads to finish.

        Args:
            timeout_ms: Maximum duration in milliseconds to wait.
            owner: Optional owner object (e.g. workspace) to restrict wait targets.
            names: Optional task names to restrict wait targets.
        """
        _logger.debug("Calling wait_for_threads (owner=%r, names=%r)...", owner, names)

        if not (app := QApplication.instance()):
            _logger.warning("No QApplication instance found")
            return

        is_main_thread = QThread.currentThread() == app.thread()
        current_fut = _current_task_future.get() or self._thread_futures.get(threading.get_ident())
        deadline = time.perf_counter() + (timeout_ms / 1000.0)

        while True:
            with self._tasks_lock:
                targets = [
                    fut
                    for fut, info in self._tracked_tasks.items()
                    if fut is not current_fut
                    and (owner is None or info.owner == owner)
                    and (names is None or info.name in names)
                ]

            if not targets:
                _logger.debug("Target thread count reached (0 remaining), breaking loop.")
                break

            if (remaining := deadline - time.perf_counter()) <= 0:
                with self._tasks_lock:
                    remaining_names = [self._tracked_tasks[f].name for f in targets if f in self._tracked_tasks]
                _logger.warning(
                    "Timeout of %dms reached while waiting for threads. "  # No fmt
                    "Proceeding while %d thread(s) are still active.",
                    timeout_ms,
                    len(targets),
                )
                _logger.debug("Remaining timed-out tasks: %r", remaining_names)
                break

            wait(targets, timeout=clamp(remaining, 0.0, 0.01))

            if is_main_thread:
                QApplication.processEvents()

        if is_main_thread:
            # Final flush to process any signals from threads that just finished
            QApplication.processEvents()

            if owner is None:
                QThreadPool.globalInstance().waitForDone(10)

        if _logger.isEnabledFor(DEBUG):
            with self._tasks_lock:
                _logger.debug("Final active threads count: %s", len(self._tracked_tasks))
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

    def log_task(f: UnifiedFuture[Any]) -> None:
        if (e := f.exception()) is None:
            return

        if isinstance(e, (Cancelled, CancelledError, asyncio.CancelledError)):
            _logger.debug("Task cancelled: %s", e)
        else:
            _logger.debug(e, exc_info=e)

    def decorator(fn: Any) -> Any:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not isinstance(loop := get_loop(), QtEventLoop):
                raise CustomTypeError("The current running loop isn't QtEventLoop")

            if iscoroutinefunction(fn):
                if QThread.currentThread() == loop.thread() and (async_loop := _get_asyncio_running_loop()):
                    task = async_loop.create_task(fn(*args, **kwargs))
                    fut = UnifiedFuture[Any].from_future(task)
                    fut.add_done_callback(log_task)
                else:
                    fut = loop.from_thread(asyncio.run, fn(*args, **kwargs))
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
def run_in_background(*, name: str | None = None, owner: object | None = None) -> _DecoratorFuture: ...
def run_in_background(func: Any = None, *, name: str | None = None, owner: object | None = None) -> Any:
    """
    Executes the decorated function in a background thread (via QThreadPool)
    using the `QtEventLoop`'s `to_thread` logic.

    Args:
        func: The function to wrap (when used as `@run_in_background` without parens)
        name: Optional thread name for logging (when used as `@run_in_background(name="...")`)
        owner: Optional owner object (e.g. workspace) for scoped wait operations.

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
            task_owner = owner if owner is not None else _detect_owner(args[0]) if args else None

            return (
                loop.to_thread_scoped(task_owner, func_name, asyncio.run, fn(*args, **kwargs))
                if iscoroutinefunction(fn)
                else loop.to_thread_scoped(task_owner, func_name, fn, *args, **kwargs)
            )

        return wrapper

    return decorator if func is None else decorator(func)


def _get_asyncio_running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _detect_owner(obj: Any) -> object:
    from ..app.workspace import BaseWorkspace

    if hasattr(obj, "workspace") and isinstance(obj.workspace, BaseWorkspace):
        return obj.workspace

    if isinstance(obj, QObject):
        p: QObject | None = obj
        while p is not None:
            if isinstance(p, BaseWorkspace):
                return p
            p = p.parent()
    return obj
