from __future__ import annotations

import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest
from pytest_mock import MockerFixture

from vsview.app.workspace.utils import EXCLUDED_PREFIXES, _get_installed_top_levels, evict_packages, find_local_packages

pytestmark = [pytest.mark.unit]


def _make_module(name: str, file: str | None) -> ModuleType:
    mod = ModuleType(name)
    mod.__file__ = file
    mod.__spec__ = ModuleSpec(name, None, origin=file)
    return mod


def test_get_installed_top_levels_includes_editable_installs(mocker: MockerFixture) -> None:
    mocker.patch("importlib.metadata.packages_distributions", return_value={"pyside6": ["pyside6"]})
    mocker.patch(
        "importlib.metadata.distributions",
        return_value=[type("D", (), {"metadata": {"Name": "vsview-comp"}})()],
    )

    result = _get_installed_top_levels()

    assert "pyside6" in result
    assert "vsview_comp" in result


def test_find_local_packages_detects_local_skips_installed(tmp_path: Path, mocker: MockerFixture) -> None:
    local_file = tmp_path / "localmod.py"
    local_file.write_text("x = 1\n")

    prefix = next(iter(EXCLUDED_PREFIXES))
    stdlib_path = str(prefix / "_asyncio.pyd")

    mocker.patch("vsview.app.workspace.utils._get_installed_top_levels", return_value=frozenset({"pyside6"}))
    mocker.patch.dict(
        sys.modules,
        {
            "localmod": _make_module("localmod", str(local_file)),
            "pyside6": _make_module("pyside6", str(tmp_path / "pyside6.py")),
            "_asyncio": _make_module("_asyncio", stdlib_path),
        },
    )

    result = find_local_packages()

    # Local module is detected
    assert "localmod" in result
    # Installed module is skipped
    assert "pyside6" not in result
    # Stdlib module is skipped
    assert "_asyncio" not in result


def test_evict_packages_removes_submodules_not_partial_matches(mocker: MockerFixture) -> None:
    mocker.patch.dict(
        sys.modules,
        {
            "foo": _make_module("foo", "/tmp/foo/__init__.py"),
            "foo.bar": _make_module("foo.bar", "/tmp/foo/bar.py"),
            "foobar": _make_module("foobar", "/tmp/foobar.py"),
        },
    )

    evict_packages(["foo"])

    # Package and submodules are evicted
    assert "foo" not in sys.modules
    assert "foo.bar" not in sys.modules
    # Unrelated package is not affected
    assert "foobar" in sys.modules
