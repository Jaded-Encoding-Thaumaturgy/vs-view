"""
Output registration API for vsview.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Sequence
from functools import wraps
from logging import getLogger
from typing import Any, Literal, SupportsFloat, assert_never, overload

import vapoursynth as vs
from jetpytools import CustomValueError, flatten, to_arr

from ..app.outputs import AudioMetadata, VideoMetadata
from ._helpers import output_metadata as _output_metadata

_logger = getLogger(__name__)

type VideoNodeIterable = Iterable[vs.VideoNode | VideoNodeIterable]
type AudioNodeIterable = Iterable[vs.AudioNode | AudioNodeIterable]
type RawNodeIterable = Iterable[vs.RawNode | RawNodeIterable]
type OutputNode = vs.VideoNode | vs.AudioNode | vs.RawNode | VideoNodeIterable | AudioNodeIterable | RawNodeIterable

# ScenesT = Keyframes | list[tuple[int, int]] | list[Keyframes | list[tuple[int, int]]] | None


# VideoNode signature
@overload
def set_output(
    node: vs.VideoNode,
    index: int = ...,
    /,
    *,
    alpha: vs.VideoNode | Literal[True] | None = ...,
    framedurs: Sequence[SupportsFloat] | None = None,
    hdr: bool | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.VideoNode,
    name: str | bool | None = ...,
    /,
    *,
    alpha: vs.VideoNode | Literal[True] | None = ...,
    framedurs: Sequence[SupportsFloat] | None = None,
    hdr: bool | None = None,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: vs.VideoNode,
    index: int = ...,
    name: str | bool | None = ...,
    /,
    alpha: vs.VideoNode | Literal[True] | None = ...,
    *,
    framedurs: Sequence[SupportsFloat] | None = None,
    hdr: bool | None = None,
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
def set_output(node: vs.RawNode, index: int | Sequence[int] = ..., /, **kwargs: Any) -> None: ...


@overload
def set_output(node: vs.RawNode, name: str | bool | None = ..., /, **kwargs: Any) -> None: ...


@overload
def set_output(
    node: VideoNodeIterable | AudioNodeIterable | RawNodeIterable,
    index: int | Sequence[int] = ...,
    /,
    **kwargs: Any,
) -> None: ...


@overload
def set_output(
    node: VideoNodeIterable | AudioNodeIterable | RawNodeIterable,
    name: str | bool | None = ...,
    /,
    **kwargs: Any,
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
    node: OutputNode,
    index_or_name: int | Sequence[int] | str | bool | None = None,
    name: str | bool | None = None,
    /,
    alpha: vs.VideoNode | Literal[True] | None = None,
    *,
    framedurs: Sequence[SupportsFloat] | None = None,
    hdr: bool | None = None,
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
        alpha: Optional alpha channel VideoNode or if True, fetch the `_Alpha` prop (only for VideoNode outputs).
        framedurs: Optional sequence of frame durations in seconds for VFR clips (only for VideoNode outputs).
        hdr: Optional boolean override for the HDR detection for VideoNode outputs.
            If passing `--hdr`, this flag can be used to disable HDR conversion for this output.
        downmix: if None (default), follows the global settings downmix of vsview if previewed
            through vsview. Otherwise True or False forces the behavior.
        **kwargs: Additional metadata passed to VSView plugins for custom configuration of this output.
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
                n.set_output(i, alpha if alpha is not True else None)
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
                if framedurs and len(framedurs) != n.num_frames:
                    raise CustomValueError(
                        "framedurs length must match number of frames", kwargs.pop("func", set_output)
                    )

                _output_metadata[file][i] = VideoMetadata(
                    effective_name or f"{title} {i}",
                    [float(f) for f in (framedurs or [])],
                    alpha is True or None,
                    hdr,
                    kwargs,
                )
            elif isinstance(n, vs.AudioNode):
                _output_metadata[file][i] = AudioMetadata(effective_name or f"{title} {i}", downmix, kwargs)

        #     if scenes:
        #         set_scening(scenes, n, effective_name or f"{title} {i}")


@overload
def catch_output[**P, N: OutputNode](func: Callable[P, N], /) -> Callable[P, N]: ...


@overload
def catch_output[**P, N: OutputNode](
    *,
    index: int | Sequence[int] = ...,
    name: str | bool | None = ...,
    alpha: vs.VideoNode | Literal[True] | None = ...,
    framedurs: Sequence[SupportsFloat] | None = ...,
    downmix: bool | None = ...,
    **kwargs: Any,
) -> Callable[[Callable[P, N]], Callable[P, N]]: ...


def catch_output[**P, N: OutputNode](
    func: Callable[P, N] | None = None,
    /,
    index: int | Sequence[int] | None = None,
    name: str | bool | None = None,
    alpha: vs.VideoNode | Literal[True] | None = None,
    *,
    framedurs: Sequence[SupportsFloat] | None = None,
    downmix: bool | None = None,
    **kwargs: Any,
) -> Callable[P, N] | Callable[[Callable[P, N]], Callable[P, N]]:
    """
    Decorator variant of `set_output()`.

    Calls the decorated function and registers its return value as an output.

    Examples:
        ```python
        @catch_output
        def source() -> vs.VideNode:
            return vs.core.bs.BestSource("video.mkv")


        @catch_output(name="Filtered", alpha=True)
        def filtered() -> vs.VideNode:
            src = vs.core.bs.BestSource("image.png")
            return src.std.BoxBlur()
        ```

    Args:
        func: The function to decorate (populated automatically when used as a bare decorator).
        index: An int or sequence of ints specifying output indices
        name: Explicit name override. If not provided, the function name is used.
        alpha: Optional alpha channel VideoNode or if True, fetch the `_Alpha` prop (only for VideoNode outputs).
        framedurs: Optional sequence of frame durations in seconds for VFR clips (only for VideoNode outputs).
        downmix: If None (default), follows the global settings downmix of vsview if previewed through vsview.
            Otherwise True or False forces the behavior.
        **kwargs: Additional metadata passed to VSView plugins for custom configuration of this output.

    Returns:
        The decorated function (its original return type is preserved).
    """

    def decorator(fn: Callable[P, N]) -> Callable[P, N]:
        @wraps(fn)
        def wrapper(*fn_args: P.args, **fn_kwargs: P.kwargs) -> N:
            nonlocal name

            result = fn(*fn_args, **fn_kwargs)

            if name is True or name is None:
                name = fn.__name__

            set_output(  # type: ignore[misc]
                result,  # type: ignore[arg-type]
                index,  # type: ignore[arg-type]
                name,
                alpha=alpha,
                framedurs=framedurs,
                downmix=downmix,
                **kwargs,
            )

            return result

        return wrapper

    if func is not None:
        return decorator(func)

    return decorator


def _resolve_var_name(obj: Any, *, frame_depth: int = 1) -> str | None:
    try:
        frame = sys._getframe(frame_depth)
    except ValueError:
        return None

    try:
        obj_id = id(obj)

        for var_name, value in reversed(list(frame.f_locals.items())):
            if id(value) == obj_id:
                return var_name

        return None
    finally:
        del frame
