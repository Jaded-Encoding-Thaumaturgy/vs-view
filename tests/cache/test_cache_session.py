from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from vsview.app.cache.session import BYTES_PER_GB, close_session, get_session, remove_orphans
from vsview.app.cache.storage import CUSTOM_ROOT_NAME, CacheSession
from vsview.app.settings import SettingsManager

pytestmark = [pytest.mark.unit]


@pytest.fixture
def scratch_directory(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "scratch"
    SettingsManager.global_settings.render_cache.scratch_directory = str(root)
    yield root
    close_session()


def test_session_is_created_in_the_configured_directory_and_follows_the_quota(scratch_directory: Path) -> None:
    settings = SettingsManager.global_settings.render_cache

    session = get_session()
    settings.max_disk_gb = 7
    same_session = get_session()

    assert session.path.parent == scratch_directory / CUSTOM_ROOT_NAME
    assert same_session is session
    assert session.quota_bytes == 7 * BYTES_PER_GB


def test_close_session_deletes_it_and_the_next_one_is_new(scratch_directory: Path) -> None:
    session = get_session()

    close_session()
    next_session = get_session()

    assert not session.path.exists()
    assert next_session is not session
    assert next_session.path.is_dir()


def test_remove_orphans_removes_dead_sessions_only(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    live = CacheSession(root, quota_bytes=1 << 30)
    crashed = root / "1-0000dead"
    crashed.mkdir()
    (root / "1-0000dead.lock").touch()
    without_lock = root / "2-00000bad"
    without_lock.mkdir()
    (root / "user folder" / "inner").mkdir(parents=True)

    removed = remove_orphans(root)

    assert sorted(removed) == sorted([crashed, without_lock])
    assert sorted(path.name for path in root.iterdir()) == sorted(
        [live.path.name, f"{live.path.name}.lock", "user folder"]
    )
    assert (root / "user folder" / "inner").is_dir()
    live.close()
