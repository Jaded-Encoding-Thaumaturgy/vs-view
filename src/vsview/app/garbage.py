"""Cyclic garbage collection that only runs on the main thread."""

from __future__ import annotations

import gc
import time
from logging import getLogger
from weakref import WeakSet

from jetpytools import Singleton, inject_self
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer
from shiboken6 import Shiboken

from ..vsenv.loop import get_qt_loop

logger = getLogger(__name__)


class GarbageCollector(Singleton):
    """Run cyclic GC from a main-thread timer, because automatic GC can delete QObjects on worker threads."""

    INTERVAL_MS = 500
    MAX_FULL_DEFERRAL_S = 60.0

    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._full_deferrers = WeakSet[QObject]()
        self._full_due_since: float | None = None

    @inject_self
    def start(self) -> None:
        """Disable automatic GC and collect from a main-thread timer instead."""
        if self._timer is not None:
            return

        gc.collect()
        # Later collections skip frozen objects, which keeps full collections short.
        # It must run before plugins load because a frozen object released later is never freed.
        gc.freeze()
        gc.disable()

        self._timer = QTimer(QCoreApplication.instance())
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self.collect_due)
        self._timer.start()

    @inject_self
    def defer_full_collections(self, owner: QObject) -> None:
        """Postpone full collections until `owner` resumes them or is deleted, up to `MAX_FULL_DEFERRAL_S`."""
        self._full_deferrers.add(owner)

    @inject_self
    def resume_full_collections(self, owner: QObject) -> None:
        """Stop postponing full collections for `owner`."""
        self._full_deferrers.discard(owner)

    @inject_self
    def collect_due(self) -> int | None:
        """
        Run the collection that automatic GC would run now, if any.

        Returns:
            The collected generation, or None if none is due.
        """
        if self._timer is not None and gc.isenabled():
            logger.warning("Automatic garbage collection was enabled again, disabling it")
            gc.disable()

        count0, count1, count2 = gc.get_count()
        threshold0, threshold1, threshold2 = gc.get_threshold()

        if count0 <= threshold0:
            return None

        generation = 2 if count2 > threshold2 else 1 if count1 > threshold1 else 0

        if generation == 2:
            if self._is_full_deferred():
                generation = 1
            else:
                self._full_due_since = None

        gc.collect(generation)
        return generation

    def _is_full_deferred(self) -> bool:
        if not any(Shiboken.isValid(owner) for owner in self._full_deferrers):
            return False

        now = time.monotonic()
        if self._full_due_since is None:
            self._full_due_since = now

        return now - self._full_due_since < self.MAX_FULL_DEFERRAL_S


def collect_garbage() -> int:
    """
    Run a full collection on the main thread and wait for it.

    Returns:
        The number of unreachable objects found.
    """
    app = QCoreApplication.instance()

    if app is None or QThread.currentThread() == app.thread():
        return gc.collect()

    return get_qt_loop().from_thread(gc.collect).result()
