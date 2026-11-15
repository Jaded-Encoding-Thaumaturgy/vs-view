from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from vsview.app.utils import atomic_write_text

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("existing", [None, "old content"])
def test_atomic_write_text_writes_content(tmp_path: Path, existing: str | None) -> None:
    target = tmp_path / "settings.json"
    if existing is not None:
        target.write_text(existing, encoding="utf-8")

    atomic_write_text(target, '{"key": "välue"}')

    assert target.read_text(encoding="utf-8") == '{"key": "välue"}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_atomic_write_text_keeps_original_when_replace_fails(tmp_path: Path, mocker: MockerFixture) -> None:
    target = tmp_path / "settings.json"
    target.write_text("old content", encoding="utf-8")
    mocker.patch.object(os, "replace", side_effect=OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "old content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]
