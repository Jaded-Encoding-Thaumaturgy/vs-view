from typing import Annotated, Any

from pydantic import BaseModel, Field

from vsview.api import (
    Checkbox,
    LineEdit,
)


class GlobalSettings(BaseModel):
    tmdb_movie_format: Annotated[
        str,
        LineEdit("Format to use when selecting a Movie from TMDB"),
    ] = "{tmdb_title} ({tmdb_year}) - {video_nodes}"
    tmdb_tv_format: Annotated[
        str,
        LineEdit("Format to use when selecting a Movie from TMDB"),
    ] = "{tmdb_title} ({tmdb_year}) - S01E01 - {video_nodes}"
    tmdb_api_key: Annotated[
        str,
        LineEdit(
            "TMDB API Key",
        ),
    ] = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIxYTczNzMzMDE5NjFkMDNmOTdmODUzYTg3NmRkMTIxMiIsInN1YiI6IjU4NjRmNTkyYzNhMzY4MGFiNjAxNzUzNCIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.gh1BwogCCKOda6xj9FRMgAAj_RYKMMPC3oNlcBtlmwk"  # noqa: E501
    p_picttype_default: Annotated[
        bool,
        Checkbox(
            label="P PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    b_picttype_default: Annotated[
        bool,
        Checkbox(
            label="B PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    i_picttype_default: Annotated[
        bool,
        Checkbox(
            label="I PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    current_frame_default: Annotated[
        bool,
        Checkbox(
            label="Current Frame Default",
            text="",
            tooltip="If checked it will enable current frame by default.",
        ),
    ] = True
    public_comp_default: Annotated[
        bool,
        Checkbox(
            label="Public Comp Default",
            text="",
            tooltip="If checked it will enable public comps by default.",
        ),
    ] = True
    open_comp_automatically: Annotated[
        bool,
        Checkbox(
            label="Open comp links automatically",
            text="",
            tooltip="Will open the link to the comp once it has finished automatically.",
        ),
    ] = False
    cookies: dict[str, Any] = Field(default_factory=dict)
