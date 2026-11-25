from __future__ import annotations

import weakref
from logging import getLogger

import vapoursynth as vs

from ..utils import download_clip
from .exceptions import CacheFrameError
from .storage import CacheSession, DiskFrameStore

logger = getLogger(__name__)


class OutputCache:
    """The render cache of one video output."""

    def __init__(self, clip: vs.VideoNode) -> None:
        """
        Raises:
            ValueError: The clip can't be cached.
        """
        self.source = download_clip(clip)
        self.store: DiskFrameStore | None = None
        self.clip = self._cached_clip(self.source)

    def ensure_store(self, session: CacheSession) -> DiskFrameStore:
        if (store := self.store) is None:
            store = self.store = session.create_store(self.source)
        return store

    def detach_store(self) -> DiskFrameStore | None:
        """Stop reading from the store and return it, so the caller can close it."""
        store, self.store = self.store, None
        return store

    def _cached_clip(self, source: vs.VideoNode) -> vs.VideoNode:
        blank = source.std.BlankClip(keep=True)
        cache_ref = weakref.ref(self)

        def select(n: int) -> vs.VideoNode:
            if (cache := cache_ref()) is None or (store := cache.store) is None or n not in store:
                return source

            try:
                frame = store.read(n)
            except CacheFrameError as e:
                logger.warning("Couldn't read frame n° %d: %s", n, e)
                return source

            return blank.std.ModifyFrame(blank, lambda n, f: frame)

        return source.std.FrameEval(select, clip_src=source)
