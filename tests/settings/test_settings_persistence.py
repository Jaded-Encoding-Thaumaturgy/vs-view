from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from pytest_mock import MockerFixture

from vsview.app.settings.enums import Resizer
from vsview.app.settings.manager import SettingsManager, validate_lenient
from vsview.app.settings.metadata import LineEdit
from vsview.app.settings.models import GlobalSettings, LocalSettings, ViewSettings

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def _zoom_factors_metadata() -> LineEdit:
    return next(m for m in ViewSettings.model_fields["zoom_factors"].metadata if isinstance(m, LineEdit))


@pytest.mark.parametrize(
    ("input_factors", "expected_factors"),
    [
        ([0.5, math.inf, 2.0], [0.5, 2.0]),
        ([math.nan, 1.5], [1.5]),
        ([math.inf, -math.inf, math.nan], [1.0]),
    ],
)
def test_view_settings_zoom_factors_drop_non_finite(input_factors: list[float], expected_factors: list[float]) -> None:
    settings = ViewSettings(zoom_factors=input_factors)

    assert settings.zoom_factors == expected_factors


@pytest.mark.parametrize(
    ("text", "expected_factors"),
    [
        ("0.5, 1, inf", [0.5, 1.0]),
        ("1e999, 2", [2.0]),
        ("nan", [1.0]),
    ],
)
def test_zoom_factors_from_ui_drops_non_finite(text: str, expected_factors: list[float]) -> None:
    metadata = _zoom_factors_metadata()
    assert metadata.from_ui is not None

    assert metadata.from_ui(text) == expected_factors


def test_zoom_factors_with_inf_survive_json_round_trip() -> None:
    data = GlobalSettings().model_dump()
    data["view"]["zoom_factors"] = [0.5, 1.0, math.inf]
    settings = GlobalSettings.model_validate(data)

    reloaded = GlobalSettings.model_validate(json.loads(settings.model_dump_json()))

    assert reloaded.view.zoom_factors == [0.5, 1.0]


UNREADABLE_GLOBAL_FILES = [
    pytest.param(b"{ unclosed invalid json: [", id="invalid-json"),
    pytest.param(json.dumps({"timeline": {"seek_step": "not_a_number"}}).encode(), id="invalid-schema"),
    pytest.param(json.dumps({"view": {"zoom_factors": [0.5, None]}}).encode(), id="null-zoom-factor"),
    pytest.param(json.dumps({"view": {"zoom_factors": [1.0]}}).encode("utf-16"), id="utf-16"),
    pytest.param(
        json.dumps({"shortcuts": [{"action_id": "x", "key_sequence": [1, 2]}]}).encode(), id="validator-type-error"
    ),
]


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.parametrize("content", UNREADABLE_GLOBAL_FILES)
def test_unreadable_global_settings_fall_back_to_defaults(
    fresh_settings_manager: Callable[[], SettingsManager], content: bytes
) -> None:
    _write_bytes(GlobalSettings.path, content)

    sm = fresh_settings_manager()

    assert sm.global_settings.model_dump_json() == GlobalSettings().model_dump_json()


@pytest.mark.parametrize("content", UNREADABLE_GLOBAL_FILES)
def test_unreadable_global_settings_are_backed_up(
    fresh_settings_manager: Callable[[], SettingsManager], content: bytes
) -> None:
    _write_bytes(GlobalSettings.path, content)

    fresh_settings_manager()

    backup = GlobalSettings.path.with_name(f"{GlobalSettings.path.name}.bak")
    assert backup.read_bytes() == content


def test_readable_global_settings_are_not_backed_up(fresh_settings_manager: Callable[[], SettingsManager]) -> None:
    _write_bytes(GlobalSettings.path, GlobalSettings().model_dump_json().encode())

    fresh_settings_manager()

    assert not GlobalSettings.path.with_name(f"{GlobalSettings.path.name}.bak").exists()


def _local_with_view(view: dict[str, object]) -> bytes:
    data = LocalSettings().model_dump(mode="json")
    data["view"] = view
    return json.dumps(data).encode()


UNREADABLE_LOCAL_FILES = [
    pytest.param(b"{ corrupt json: [", id="invalid-json"),
    pytest.param(json.dumps({"last_frame": "not_an_int"}).encode(), id="invalid-schema"),
    pytest.param(json.dumps({"last_frame": 5}).encode("utf-16"), id="utf-16"),
]


@pytest.mark.parametrize("content", UNREADABLE_LOCAL_FILES)
def test_unreadable_local_settings_fall_back_to_defaults_and_are_backed_up(tmp_path: Path, content: bytes) -> None:
    script = tmp_path / "script.vpy"
    script.touch()
    settings_path = SettingsManager.local_settings_path(script)
    _write_bytes(settings_path, content)

    settings = SettingsManager.get_local_settings(script)

    assert settings.model_dump_json() == LocalSettings(source_path=str(script)).model_dump_json()
    assert settings_path.with_name(f"{settings_path.name}.bak").read_bytes() == content


def test_invalid_global_field_is_reset_and_the_other_fields_are_kept(
    fresh_settings_manager: Callable[[], SettingsManager],
) -> None:
    data = json.loads(GlobalSettings().model_dump_json())
    data["timeline"]["seek_step"] = "not_a_number"
    data["status_message_timeout"] = 1234
    content = json.dumps(data).encode()
    _write_bytes(GlobalSettings.path, content)

    sm = fresh_settings_manager()

    assert sm.global_settings.timeline.seek_step == GlobalSettings().timeline.seek_step
    assert sm.global_settings.status_message_timeout == 1234
    assert GlobalSettings.path.with_name(f"{GlobalSettings.path.name}.bak").read_bytes() == content


def test_invalid_local_field_is_reset_and_the_other_fields_are_kept(tmp_path: Path) -> None:
    script = tmp_path / "script.vpy"
    script.touch()
    settings_path = SettingsManager.local_settings_path(script)
    data = json.loads(LocalSettings(source_path=str(script), last_frame=7).model_dump_json())
    data["view"] = {"last_center": [None, None], "last_zoom": 2.0}
    content = json.dumps(data).encode()
    _write_bytes(settings_path, content)

    settings = SettingsManager.get_local_settings(script)

    assert (settings.last_frame, settings.view.last_zoom, settings.view.last_center) == (7, 2.0, None)
    assert settings_path.with_name(f"{settings_path.name}.bak").read_bytes() == content


@pytest.mark.parametrize("resizer", list(Resizer), ids=lambda r: r.name)
def test_chroma_resizer_stored_by_label_loads_and_is_saved_by_id(
    fresh_settings_manager: Callable[[], SettingsManager], resizer: Resizer
) -> None:
    _write_bytes(GlobalSettings.path, json.dumps({"view": {"chroma_resizer": resizer.label}}).encode())

    sm = fresh_settings_manager()
    sm.save_global()

    assert sm.global_settings.view.chroma_resizer is resizer
    assert json.loads(GlobalSettings.path.read_text(encoding="utf-8"))["view"]["chroma_resizer"] == resizer.name.lower()


class _Item(BaseModel):
    name: str
    enabled: bool = False


class _Section(BaseModel):
    value: int = 1
    other: int = 2


class _Lenient(BaseModel):
    required: str
    section: _Section = _Section()
    items: list[_Item] = []


@pytest.mark.parametrize(
    ("data", "expected", "dropped"),
    [
        pytest.param(
            {"required": "x", "section": {"value": "bad", "other": 5}},
            _Lenient(required="x", section=_Section(other=5)),
            ["section.value"],
            id="nested-field",
        ),
        pytest.param(
            {"required": "x", "items": [{"name": "a", "enabled": "maybe"}, {"name": "b", "enabled": True}]},
            _Lenient(required="x", items=[_Item(name="a"), _Item(name="b", enabled=True)]),
            ["items.0.enabled"],
            id="field-of-a-list-item",
        ),
        pytest.param(
            {"required": "x", "items": [{"enabled": True}]},
            _Lenient(required="x"),
            ["items"],
            id="list-item-without-a-required-field",
        ),
    ],
)
def test_validate_lenient_resets_only_the_invalid_fields(
    data: dict[str, Any], expected: _Lenient, dropped: list[str]
) -> None:
    original = json.dumps(data)

    assert validate_lenient(_Lenient, data) == (expected, dropped)
    assert json.dumps(data) == original


def test_validate_lenient_raises_when_a_required_field_is_missing() -> None:
    with pytest.raises(ValidationError, match="required"):
        validate_lenient(_Lenient, {"section": {"value": "bad"}})


def test_failed_global_save_keeps_previous_file(mocker: MockerFixture) -> None:
    SettingsManager.save_global(GlobalSettings())
    previous = GlobalSettings.path.read_bytes()
    changed = GlobalSettings()
    changed.status_message_timeout += 1
    mocker.patch.object(os, "replace", side_effect=OSError("disk full"))

    SettingsManager.save_global(changed)

    assert GlobalSettings.path.read_bytes() == previous


def test_failed_local_save_keeps_previous_file(tmp_path: Path, mocker: MockerFixture) -> None:
    script = tmp_path / "script.vpy"
    script.touch()
    SettingsManager.save_local(script, LocalSettings(source_path=str(script), last_frame=1))
    settings_path = SettingsManager.local_settings_path(script)
    previous = settings_path.read_bytes()
    mocker.patch.object(os, "replace", side_effect=OSError("disk full"))

    SettingsManager.save_local(script, LocalSettings(source_path=str(script), last_frame=2))

    assert settings_path.read_bytes() == previous
