import os
from logging import getLogger

_TRUTHY = frozenset({"1", "true", "yes", "on"})

logger = getLogger(__name__)


def getenv_bool(key: str, default: bool = False) -> bool:
    return default if (val := os.getenv(key)) is None else val.strip().lower() in _TRUTHY
