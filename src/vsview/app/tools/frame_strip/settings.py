from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from vsview.api import Checkbox, LocalSettingsModel, Spin

from .constants import MAX_NEIGHBOR_COUNT


class GlobalSettings(BaseModel):
    neighbor_count: Annotated[
        int,
        Spin(
            label="Neighbor count",
            min=1,
            max=MAX_NEIGHBOR_COUNT,
            tooltip="Number of previous and next frames to display",
        ),
    ] = 2

    step: Annotated[
        int,
        Spin(label="Frame step", min=1, max=1000, tooltip="Frame stride between adjacent slots"),
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
    neighbor_count: int | None = None
    step: int | None = None
    show_current_frame: bool | None = None
