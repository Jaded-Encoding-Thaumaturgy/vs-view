import sys
from pathlib import Path
from typing import override

from PySide6.QtCore import QMimeData, QUrl

VIRTUAL_NAMES = frozenset({"script.py", "workspace.code-workspace"})
SUPPORTED_SCRIPT_SUFFIXES = frozenset({".py", ".vpy"})


def extract_supported_script_files(mime_data: QMimeData) -> list[Path]:
    """Extract valid local .py/.vpy script file paths from QMimeData."""
    files = list[Path]()
    for url in mime_data.urls():
        if url.isLocalFile():
            p = Path(url.toLocalFile())
            if p.is_file() and p.suffix.lower() in SUPPORTED_SCRIPT_SUFFIXES:
                files.append(p)
    return files


class WorkspaceUri(QUrl):
    @property
    def local_path(self) -> Path | None:
        if self.scheme() != "file":
            return None

        if not (local_file := self.toLocalFile()):
            return None

        if (p := Path(local_file)).exists():
            return p

        if local_file.replace("\\", "/").startswith("/workspace/") and (
            p.name in VIRTUAL_NAMES or p.name.startswith("untitled_")
        ):
            return None

        if sys.platform == "win32" and not (len(local_file) >= 2 and local_file[1] == ":"):
            return None

        return p


class ContentPath:
    __slots__ = ("code", "filename")

    def __init__(self, code: str, filename: str) -> None:
        self.code = code
        self.filename = filename

    def __fspath__(self) -> str:
        return self.filename

    def __len__(self) -> int:
        return len(self.code)

    @override
    def __str__(self) -> str:
        return self.code

    @override
    def __repr__(self) -> str:
        return self.filename

    def splitlines(self, keepends: bool = False) -> list[str]:
        return self.code.splitlines(keepends)
