from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from vsview.app.utils import atomic_open, atomic_write

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("existing", [None, "old content"])
def test_atomic_write_writes_content(tmp_path: Path, existing: str | None) -> None:
    target = tmp_path / "settings.json"
    if existing is not None:
        target.write_text(existing, encoding="utf-8")

    atomic_write(target, '{"key": "välue"}')

    assert target.read_text(encoding="utf-8") == '{"key": "välue"}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_atomic_write_keeps_original_when_replace_fails(tmp_path: Path, mocker: MockerFixture) -> None:
    target = tmp_path / "settings.json"
    target.write_text("old content", encoding="utf-8")
    mocker.patch.object(os, "replace", side_effect=OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        atomic_write(target, "new content")

    assert target.read_text(encoding="utf-8") == "old content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


@pytest.mark.parametrize(
    ("newline", "expected"),
    [("", b"a\r\nb\n"), ("\n", b"a\r\nb\n"), (None, f"a\r{os.linesep}b{os.linesep}".encode())],
)
def test_atomic_write_translates_newlines_like_open(tmp_path: Path, newline: str | None, expected: bytes) -> None:
    target = tmp_path / "script.vpy"

    atomic_write(target, "a\r\nb\n", newline=newline)

    assert target.read_bytes() == expected


def test_atomic_write_bytes_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "scene.txt"
    target.write_bytes(b"old")

    atomic_write(target, b"\x00new\xff")

    assert target.read_bytes() == b"\x00new\xff"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scene.txt"]


def test_atomic_open_keeps_original_when_the_writer_raises(tmp_path: Path) -> None:
    target = tmp_path / "scene.txt"
    target.write_bytes(b"old")

    def write_then_fail() -> None:
        with atomic_open(target) as f:
            f.write(b"partial")
            raise RuntimeError("serializer failed")

    with pytest.raises(RuntimeError, match="serializer failed"):
        write_then_fail()

    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scene.txt"]


def test_atomic_write_refuses_read_only_target(tmp_path: Path) -> None:
    target = tmp_path / "script.vpy"
    target.write_bytes(b"old")
    target.chmod(stat.S_IREAD)

    try:
        with pytest.raises(PermissionError, match="read-only"):
            atomic_write(target, "new")
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["script.vpy"]


def test_atomic_write_refuses_directory_target(tmp_path: Path) -> None:
    target = tmp_path / "folder"
    target.mkdir()

    with pytest.raises(IsADirectoryError, match="Is a directory"):
        atomic_write(target, b"new")

    assert target.is_dir()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["folder"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows only maps the read-only bit")
def test_atomic_write_keeps_the_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_bytes(b"old")
    target.chmod(0o751)

    atomic_write(target, b"new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o751


def test_atomic_write_through_a_symlink_keeps_the_link(tmp_path: Path) -> None:
    real = tmp_path / "real.vpy"
    real.write_bytes(b"old")
    link = tmp_path / "link.vpy"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks are not available")

    atomic_write(link, "new")

    assert link.is_symlink()
    assert real.read_bytes() == b"new"
