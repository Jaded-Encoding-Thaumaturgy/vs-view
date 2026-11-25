class RenderCacheError(Exception):
    """Base class for render cache errors."""


class CacheFrameError(RenderCacheError):
    """A frame is not cached, or its file is missing or can't be read."""


class QuotaExceededError(RenderCacheError):
    """Writing a frame would go over the disk quota of the session."""
