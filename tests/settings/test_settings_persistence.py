from __future__ import annotations

import json
import math

import pytest

from vsview.app.settings.metadata import LineEdit
from vsview.app.settings.models import GlobalSettings, ViewSettings

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
