from __future__ import annotations

from enum import Enum, IntEnum, auto
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

    RENDERING = auto()
    """The outputs of the content being loaded exist, and its first frame is rendering."""

    LOADED = auto()
    """The content is loaded and shown."""

    REPLACING = auto()
    """
    A load or reload waits for the loaded content's tasks to end.

    The loaded content is still held, so the load can be cancelled and the content kept.
    """

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
                return frozenset({Phase.RENDERING, Phase.FAILED, Phase.CLOSING})
            case Phase.RENDERING:
                return frozenset({Phase.LOADED, Phase.FAILED, Phase.CLOSING})
            case Phase.LOADED:
                return frozenset({Phase.REPLACING, Phase.FAILED, Phase.CLOSING})
            case Phase.REPLACING:
                return frozenset({Phase.LOADING, Phase.LOADED, Phase.FAILED, Phase.CLOSING})
            case Phase.FAILED:
                return frozenset({Phase.LOADING, Phase.CLOSING})
            case Phase.CLOSING:
                return frozenset({Phase.CLOSED})
            case Phase.CLOSED:
                return frozenset()

    @property
    def holdable(self) -> bool:
        """Whether a plugin can hold the workspace in this phase."""
        return self in (Phase.EMPTY, Phase.RENDERING, Phase.LOADED, Phase.FAILED)


class LoadResult(IntEnum):
    """The result of a load or reload."""

    RENDERING = 0
    """The new content is rendering its first frame."""

    FAILED = 1
    """The load failed, and the error page is shown."""

    STOPPED = 2
    """The load was refused, or stopped because the workspace is closing."""


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
    """Tracks a loader workspace's phase, generation, and plugin holds."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._phase = Phase.EMPTY
        self._generation = 0
        self._holders = QObjectCounter[QObject]()

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

    def try_begin_load(self) -> Phase | None:
        """
        Start loading content.

        A workspace that holds loaded content moves to `REPLACING`, any other one to `LOADING`.

        Returns:
            The phase the workspace moved to.
            None while another load runs, once the workspace is closing,
            or when loaded content would be replaced while a plugin holds the workspace.
        """
        with self._lock:
            # REPLACING can also move to LOADING, but only through `commit_replacement`.
            match self._phase:
                case Phase.LOADED if not self._holders:
                    target = Phase.REPLACING
                case Phase.EMPTY | Phase.FAILED:
                    target = Phase.LOADING
                case _:
                    return None

            self._phase = target

        return target

    def try_begin_reload(self) -> bool:
        """
        Start reloading the loaded content.

        Returns:
            True when the workspace moved from `LOADED` to `REPLACING`,
            False otherwise or when a plugin holds the workspace.
        """
        with self._lock:
            if self._phase is not Phase.LOADED or self._holders:
                return False

            self._phase = Phase.REPLACING

        return True

    def cancel_reload(self) -> bool:
        """
        Give up a replacement before the loaded content is released, and keep that content.

        Returns:
            True when the workspace moved from `REPLACING` back to `LOADED`, False otherwise.
        """
        return self._move(Phase.LOADED, source=Phase.REPLACING)

    def commit_replacement(self) -> bool:
        """
        Mark the loaded content as released. From here on, the replacement continues as a load.

        Returns:
            True when the workspace moved from `REPLACING` to `LOADING`, False otherwise.
        """
        return self._move(Phase.LOADING, source=Phase.REPLACING)

    def next_generation(self) -> Generation:
        """Start a new generation. Every older `Generation` becomes stale."""
        with self._lock:
            self._generation += 1
            return Generation(self, self._generation)

    def begin_rendering(self) -> bool:
        """
        Mark the new outputs of a load as created. Plugins can hold the workspace again.

        Returns:
            True when the workspace moved from `LOADING` to `RENDERING`, False otherwise.
        """
        return self._move(Phase.RENDERING)

    def finish(self, generation: Generation) -> bool:
        """
        Mark the content of `generation` as loaded.

        Returns:
            False when `generation` is stale or no first frame is rendering, True otherwise.
        """
        return self._move(Phase.LOADED, source=Phase.RENDERING, generation=generation)

    def fail(self, *, loaded_only: bool = False) -> bool:
        """
        Mark the workspace as failed.

        Args:
            loaded_only: Fail only loaded content. A frame of the loaded content that fails to render sets it,
                because a load or reload that runs reports its own failure.

        Returns:
            False when the workspace has already failed or is closing,
            or when `loaded_only` is set and no content is loaded. True otherwise.
        """
        return self._move(Phase.FAILED, source=Phase.LOADED if loaded_only else None)

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
            if not self._phase.holdable:
                return False

            self._holders.add(holder)

        return True

    def release_hold(self, holder: QObject) -> None:
        """Release one hold of `holder`."""
        with self._lock:
            self._holders.discard(holder)

    def _move(self, target: Phase, *, source: Phase | None = None, generation: Generation | None = None) -> bool:
        with self._lock:
            if target not in self._phase.transition or (source is not None and self._phase is not source):
                return False

            if generation is not None and generation.value != self._generation:
                return False

            self._phase = target

        return True
