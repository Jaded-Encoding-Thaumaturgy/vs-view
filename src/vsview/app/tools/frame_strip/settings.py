from __future__ import annotations

from typing import Annotated

from jetpytools import clamp
from pydantic import AfterValidator, BaseModel

from vsview.api import Checkbox, LocalSettingsModel, Spin

from .constants import MAX_NEIGHBOR_COUNT, MAX_STEP


def limit_neighbor_count(value: int | None) -> int | None:
    return None if value is None else clamp(value, 1, MAX_NEIGHBOR_COUNT)


def limit_step(value: int | None) -> int | None:
    return None if value is None else clamp(value, 1, MAX_STEP)


class GlobalSettings(BaseModel):
    neighbor_count: Annotated[
        int,
        AfterValidator(limit_neighbor_count),
        Spin(
            label="Neighbor count",
            min=1,
            max=MAX_NEIGHBOR_COUNT,
            tooltip="Number of previous and next frames to display",
        ),
    ] = 2

    step: Annotated[
        int,
        AfterValidator(limit_step),
        Spin(label="Frame step", min=1, max=MAX_STEP, tooltip="Frame stride between adjacent slots"),
    ] = 1

    show_current_frame: Annotated[
        bool,
        Checkbox(
            label="Show current frame",
            text="Include current frame in the center",
            tooltip="Display the active frame in the center of the strip",
        ),
    ] = False


class LocalSettings(LocalSettingsModel):
    neighbor_count: Annotated[int | None, AfterValidator(limit_neighbor_count)] = None
    step: Annotated[int | None, AfterValidator(limit_step)] = None
    show_current_frame: bool | None = None
