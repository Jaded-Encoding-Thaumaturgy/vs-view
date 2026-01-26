"""
Output registration API for vsview.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from logging import getLogger
from types import FrameType
from typing import Any, assert_never, overload

import vapoursynth as vs
from jetpytools import flatten, to_arr

from ._helpers import AudioMetadata, VideoMetadata
from ._helpers import output_metadata as _output_metadata

__all__ = ["set_output"]

_logger = getLogger(__name__)

type VideoNodeIterable = Iterable[vs.VideoNode | VideoNodeIterable]
type AudioNodeIterable = Iterable[vs.AudioNode | AudioNodeIterable]

# TimecodesT = (
#     str
#     | PathLike[str]
#     | dict[tuple[int | None, int | None], float | tuple[int, int] | Fraction]
#     | list[Fraction]
#     | None
# )
# ScenesT = Keyframes | list[tuple[int, int]] | list[Keyframes | list[tuple[int, int]]] | None


# VideoNode signature
@overload
def set_output(
    node: vs.VideoNode,
    index: int = ...,
    /,
    *,
    alpha: vs.VideoNode | None = ...,
    # timecodes: TimecodesT = None,
    # denominator: int = 1001,
    # scenes: ScenesT = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.VideoNode,
    name: str | bool | None = ...,
    /,
    *,
    alpha: vs.VideoNode | None = ...,
    # timecodes: TimecodesT = None,
    # denominator: int = 1001,
    # scenes: ScenesT = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.VideoNode,
    index: int = ...,
    name: str | bool | None = ...,
    /,
    alpha: vs.VideoNode | None = ...,
    # *,
    # timecodes: TimecodesT = None,
    # denominator: int = 1001,
    # scenes: ScenesT = None,
    **kwargs: Any,
) -> None: ...


# AudioNode signature
@overload
def set_output(
    node: vs.AudioNode,
    index: int = ...,
    /,
    *,
    downmix: bool | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.AudioNode,
    name: str | bool | None = ...,
    /,
    *,
    downmix: bool | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.AudioNode,
    index: int = ...,
    name: str | bool | None = ...,
    /,
    *,
    downmix: bool | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: VideoNodeIterable | AudioNodeIterable, index: int | Sequence[int] = ..., /, **kwargs: Any
) -> None: ...


@overload
def set_output(
    node: VideoNodeIterable | AudioNodeIterable, name: str | bool | None = ..., /, **kwargs: Any
) -> None: ...


@overload
def set_output(
    node: VideoNodeIterable | AudioNodeIterable,
    index: int | Sequence[int] = ...,
    name: str | bool | None = ...,
    /,
    **kwargs: Any,
) -> None: ...


def set_output(
    node: vs.VideoNode | vs.AudioNode | VideoNodeIterable | AudioNodeIterable,
    index_or_name: int | Sequence[int] | str | bool | None = None,
    name: str | bool | None = None,
    /,
    alpha: vs.VideoNode | None = None,
    # *,
    # timecodes: TimecodesT = None,
    # denominator: int = 1001,
    # scenes: ScenesT = None,
    *,
    downmix: bool | None = None,
    **kwargs: Any,
) -> None:
    """
    Register one or more VapourSynth nodes as outputs for preview.

    This function sets the output(s) and registers metadata for tab naming in vsview.
    If no index is provided, outputs are assigned to the next available indices.

    Examples:
        ```python
        set_output(clip)  # Auto-index, auto-name ("clip")
        set_output(clip, 0)  # Index 0, auto-name
        set_output(clip, 0, "My Clip")  # Index 0, explicit name
        set_output(clip, "Source")  # Auto-index, explicit name
        set_output([clip1, clip2])  # Multiple outputs
        ```

    Args:
        node: A VideoNode, AudioNode, or iterable of nodes to output.
        index_or_name: Either:

               - An int or sequence of ints specifying output indices
               - A str to use as the output name
               - True/None to auto-detect the variable name
               - False to disable name detection

        name: Explicit name override. If provided when index_or_name is an int,
            this sets the display name for the output.
        alpha: Optional alpha channel VideoNode (only for VideoNode outputs).
        downmix: if None (default), follows the global settings downmix of vsview if previewed
            through vsview. Otherwise True or False forces the behavior.
        **kwargs: Additional keyword arguments (reserved for future use).
    """
    if isinstance(index_or_name, (str, bool)):
        index = None
        name = index_or_name
    else:
        index = index_or_name

    outputs = vs.get_outputs()
    nodes = list[vs.VideoNode | vs.AudioNode](flatten([node]))

    indices = to_arr(index) if index is not None else [max(outputs, default=-1) + 1]

    while len(indices) < len(nodes):
        indices.append(indices[-1] + 1)

    frame_depth = kwargs.pop("frame_depth", 1) + 1
    script_module = sys.modules.get("__vsview__")

    for i, n in zip(indices[: len(nodes)], nodes):
        if i in outputs:
            _logger.warning("Output index %d already in use; overwriting.", i)

        match n:
            case vs.VideoNode():
                n.set_output(i, alpha)
                title = "Clip"
            case vs.AudioNode():
                n.set_output(i)
                title = "Audio"
            case _:
                assert_never(n)

        if not script_module:
            continue

        effective_name: str | None

        match name:
            case True | None:
                effective_name = _resolve_var_name(n, frame_depth=frame_depth)
            case False:
                effective_name = None
            case str():
                effective_name = name

        if file := getattr(script_module, "__file__", None):
            if isinstance(n, vs.VideoNode):
                _output_metadata[file][i] = VideoMetadata(effective_name or f"{title} {i}", alpha)
            elif isinstance(n, vs.AudioNode):
                _output_metadata[file][i] = AudioMetadata(effective_name or f"{title} {i}", downmix)

        # if isinstance(n, vs.VideoNode):
        #     if timecodes:
        #         timecodes = str(timecodes) if not isinstance(timecodes, (dict, list)) else timecodes
        #         set_timecodes(i, timecodes, n, denominator)

        #     if scenes:
        #         set_scening(scenes, n, effective_name or f"{title} {i}")


def _resolve_var_name(obj: Any, *, frame_depth: int = 1) -> str | None:
    import inspect

    frame = inspect.currentframe()
    frames = list[FrameType]()
    locals_copy = dict[str, Any]()

    try:
        for _ in range(frame_depth):
            if not frame or not frame.f_back:
                return None

            frames.append(frame)
            frame = frame.f_back

        locals_copy = frame.f_locals.copy() if frame else {}

        obj_id = id(obj)

        return next((var_name for var_name, value in reversed(locals_copy.items()) if id(value) == obj_id), None)

    finally:
        for fr in frames:
            del fr
        del frame
        del frames
        del locals_copy
