from pathlib import Path

from platformdirs import user_cache_path


def get_stubs_dir() -> Path:
    return user_cache_path("vsview", appauthor=False) / "stubs"
