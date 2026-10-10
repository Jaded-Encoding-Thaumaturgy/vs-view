from __future__ import annotations

from pathlib import Path

import pytest

from vsview.api._helpers import DefaultUserDict, OutputMetadata

pytestmark = [pytest.mark.unit]


def test_default_user_dict() -> None:
    d = DefaultUserDict[str, list[int]](list)
    assert d["missing"] == []
    d["missing"].append(1)
    assert d["missing"] == [1]

    # pop
    assert d.pop("missing") == [1]
    assert d.pop("missing", [2]) == [2]

    # popitem
    d["a"] = [10]
    k, v = d.popitem()
    assert k == "a"
    assert v == [10]

    # clear
    d["b"] = [20]
    assert len(d) == 1
    d.clear()
    assert len(d) == 0


def test_output_metadata_mapping_operations(tmp_path: Path) -> None:
    meta = OutputMetadata(dict)
    file1 = tmp_path / "video1.mp4"
    file2 = tmp_path / "video2.mp4"

    meta[file1] = {0: "meta1"}
    assert file1 in meta
    assert str(file1) in meta
    assert meta[file1] == {0: "meta1"}
    assert meta[str(file1)] == {0: "meta1"}

    # del
    meta[file2] = {0: "meta2"}
    del meta[file1]
    assert file1 not in meta
    assert file2 in meta

    # pop
    val = meta.pop(file2)
    assert val == {0: "meta2"}
    assert file2 not in meta
    assert meta.pop(file2, {"default": "meta"}) == {"default": "meta"}

    # clear does not hang and empties mapping
    meta[file1] = {0: "meta1"}
    meta[file2] = {0: "meta2"}
    assert len(meta) == 2
    meta.clear()
    assert len(meta) == 0
