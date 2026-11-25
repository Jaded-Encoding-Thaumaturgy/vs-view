from __future__ import annotations

import re
import shutil
import threading
from contextlib import suppress
from pathlib import Path

from ..settings import SettingsManager
from .io import LockPath
from .storage import CUSTOM_ROOT_NAME, CacheSession, default_root

BYTES_PER_GB = 1000**3
# The cache directory can be any folder the user picks, so the cleanup only touches entries named like a session.
SESSION_NAME = re.compile(r"\d+-[0-9a-f]{8}")

_lock = threading.Lock()
_session: CacheSession | None = None


def get_session() -> CacheSession:
    """
    Return the render cache session of this process. It is created on first use, with the current settings.

    The quota follows the current settings each time. A new cache directory applies after a restart.

    Raises:
        RenderCacheError: The session lock can't be taken.
        OSError: The cache directory can't be created.
    """
    global _session

    settings = SettingsManager.global_settings.render_cache

    with _lock:
        if _session is None:
            root = (
                Path(settings.scratch_directory).expanduser() / CUSTOM_ROOT_NAME
                if settings.scratch_directory
                else default_root()
            )
            _session = CacheSession(root, settings.max_disk_gb * BYTES_PER_GB)

            orphan_thread = threading.Thread(target=lambda: remove_orphans(root), name="CacheCleanup", daemon=True)
            orphan_thread.start()
        else:
            _session.quota_bytes = settings.max_disk_gb * BYTES_PER_GB

        return _session


def close_session() -> None:
    """Delete the session of this process, if there is one."""
    global _session

    with _lock:
        session, _session = _session, None

    if session is not None:
        session.close()


def remove_orphans(root: Path) -> list[Path]:
    if not root.is_dir():
        return []

    removed = list[Path]()

    for entry in root.iterdir():
        with suppress(OSError):
            if SESSION_NAME.fullmatch(entry.name) and entry.is_dir() and _remove_session_dir(entry):
                removed.append(entry)

    return removed


def _remove_session_dir(path: Path) -> bool:
    lock_path = LockPath(path.with_name(f"{path.name}.lock"))

    if lock_path.path.exists():
        if not lock_path.acquire():
            return False

        shutil.rmtree(path, ignore_errors=True)
        lock_path.release()
    else:
        shutil.rmtree(path, ignore_errors=True)

    return True
