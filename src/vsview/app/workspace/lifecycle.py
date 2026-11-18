from __future__ import annotations

from enum import Enum, auto
from threading import Lock

from PySide6.QtCore import QObject
from vsengine.loops import Cancelled

from ..utils import QObjectCounter


class Phase(Enum):
    """The lifecycle phase of a loader workspace."""

    EMPTY = auto()
    """Nothing has been loaded yet."""

    LOADING = auto()
    """Content is loading into a workspace that holds no loaded content."""

    LOADED = auto()
    """The content is loaded and shown."""

    RELOADING = auto()
    """Content is loading into a workspace that holds loaded content."""

    FAILED = auto()
    """The last load failed, and the error page is shown."""

    CLOSING = auto()
    """The workspace is being torn down."""

    CLOSED = auto()
    """The teardown has finished."""

    @property
    def transition(self) -> frozenset[Phase]:
        match self:
            case Phase.EMPTY:
                return frozenset({Phase.LOADING, Phase.FAILED, Phase.CLOSING})
            case Phase.LOADING:
                return frozenset({Phase.LOADED, Phase.FAILED, Phase.CLOSING})
            case Phase.LOADED:
                return frozenset({Phase.RELOADING, Phase.FAILED, Phase.CLOSING})
            case Phase.RELOADING:
                return frozenset({Phase.LOADED, Phase.FAILED, Phase.CLOSING})
            case Phase.FAILED:
                return frozenset({Phase.LOADING, Phase.CLOSING})
            case Phase.CLOSING:
                return frozenset({Phase.CLOSED})
            case Phase.CLOSED:
                return frozenset()


class StaleGenerationError(Cancelled):
    """Raised by `Generation.check` when a newer load, reload or close has started."""


class Generation:
    """The generation a load or reload belongs to."""

    __slots__ = ("_lifecycle", "value")

    def __init__(self, lifecycle: WorkspaceLifecycle, value: int) -> None:
        self._lifecycle = lifecycle
        self.value = value

    def is_current(self) -> bool:
        """Return whether no newer load, reload or close has started since this generation."""
        return self._lifecycle.generation == self.value

    def check(self) -> None:
        """Raise `StaleGenerationError` when this generation is no longer current."""
        if not self.is_current():
            raise StaleGenerationError(f"Generation {self.value} is stale")


class WorkspaceLifecycle:
    """
    Tracks a loader workspace's phase, generation, and plugin gate.

    `Generation` increments when loading new content or closing.
    Work from an older generation is stale and must discard its results.

    Plugins hold the workspace via `WorkspaceBlocker`, which blocks content replacement,
    reloads, and teardown.
    It cannot be acquired until new outputs exist or teardown finishes.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._phase = Phase.EMPTY
        self._generation = 0
        self._holders = QObjectCounter[QObject]()
        self._exclusive = False

    @property
    def phase(self) -> Phase:
        with self._lock:
            return self._phase

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def closing(self) -> bool:
        return self.phase in (Phase.CLOSING, Phase.CLOSED)

    @property
    def busy(self) -> bool:
        """Whether a plugin holds the workspace."""
        with self._lock:
            return bool(self._holders)

    def try_begin_load(self) -> bool:
        """
        Start loading content.

        A workspace that holds loaded content moves to `RELOADING`, any other one to `LOADING`.

        Returns:
            False while another load runs, once the workspace is closing,
            or when loaded content would be replaced while a plugin holds the workspace. True otherwise.
        """
        with self._lock:
            current = self._phase
            target = Phase.RELOADING if current is Phase.LOADED else Phase.LOADING

            if target not in current.transition or (target is Phase.RELOADING and self._holders):
                return False

            self._phase = target
            self._exclusive = True

        return True

    def try_begin_reload(self) -> bool:
        """
        Start reloading the loaded content.

        Returns:
            True when the workspace moved from `LOADED` to `RELOADING`,
            False otherwise or when a plugin holds the workspace.
        """
        with self._lock:
            if self._phase is not Phase.LOADED or self._holders:
                return False

            self._phase = Phase.RELOADING
            self._exclusive = True

        return True

    def cancel_reload(self) -> bool:
        """
        Give up a reload before its new generation starts, and keep the loaded content.

        Returns:
            True when the workspace moved from `RELOADING` back to `LOADED`, False otherwise.
        """
        with self._lock:
            if self._phase is not Phase.RELOADING:
                return False

            self._phase = Phase.LOADED
            self._exclusive = False

        return True

    def next_generation(self) -> Generation:
        """Start a new generation. Every older `Generation` becomes stale."""
        with self._lock:
            self._generation += 1
            return Generation(self, self._generation)

    def end_exclusive(self) -> None:
        """Let plugins hold the workspace again, once the new outputs of a load exist."""
        with self._lock:
            if self._phase not in (Phase.CLOSING, Phase.CLOSED):
                self._exclusive = False

    def finish(self, generation: Generation) -> bool:
        """
        Mark the content of `generation` as loaded.

        Returns:
            False when `generation` is stale or no load is running, True otherwise.
        """
        return self._move(Phase.LOADED, generation)

    def fail(self) -> bool:
        """
        Mark the workspace as failed.

        Returns:
            False when the workspace has already failed or is closing, True otherwise.
        """
        return self._move(Phase.FAILED)

    def begin_close(self) -> Generation | None:
        """
        Start the teardown. Every older `Generation` becomes stale, and no plugin can hold the workspace anymore.

        Returns:
            The generation of the teardown, or None when the teardown has already started.
        """
        with self._lock:
            if Phase.CLOSING not in self._phase.transition:
                return None

            self._phase = Phase.CLOSING
            self._exclusive = True
            self._generation += 1
            return Generation(self, self._generation)

    def mark_closed(self) -> bool:
        """
        Mark the teardown as finished.

        Returns:
            False when no teardown is running, True otherwise.
        """
        return self._move(Phase.CLOSED)

    def try_hold(self, holder: QObject) -> bool:
        """
        Mark the workspace as held by `holder`, until `release_hold` or until `holder` is destroyed.

        Returns:
            False while a load or reload has not created its outputs yet, or once the workspace is closing.
        """
        with self._lock:
            if self._exclusive:
                return False

            self._holders.add(holder)

        return True

    def release_hold(self, holder: QObject) -> None:
        """Release one hold of `holder`."""
        with self._lock:
            self._holders.discard(holder)

    def _move(self, target: Phase, generation: Generation | None = None) -> bool:
        with self._lock:
            if target not in self._phase.transition:
                return False

            if generation is not None and generation.value != self._generation:
                return False

            self._phase = target

            if target in (Phase.LOADED, Phase.FAILED):
                self._exclusive = False

        return True
