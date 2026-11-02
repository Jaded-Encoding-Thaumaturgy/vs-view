from __future__ import annotations

import json
from collections.abc import Generator, Sequence
from itertools import count, islice
from logging import getLogger
from typing import Any

import vsgraph

logger = getLogger(__name__)


def format_timing(nanoseconds: int) -> str:
    if nanoseconds <= 0:
        return ""
    if nanoseconds < 1_000_000:
        return f"{nanoseconds} ns"
    return f"{nanoseconds / 1_000_000:.2f} ms"


def format_python_value(value: Any) -> str:
    match value:
        case None:
            return "None"
        case int() | bool() | float():
            return str(value)
        case str():
            return json.dumps(value)
        case vsgraph.NodeRef() | vsgraph.FrameInfo() | vsgraph.FuncRef():
            return format_python_value(value.to_dict())
        case Sequence():
            return f"[{', '.join(format_python_value(item) for item in value)}]"
        case dict():
            entries = [f"{json.dumps(str(k))}: {format_python_value(v)}" for k, v in value.items()]
            return f"{{{', '.join(entries)}}}"
        case _:
            logger.warning("Invalid Python value %r", value)
            return "<invalid>"


def build_filter_call(node: vsgraph.Node[Any]) -> str:
    """Reconstruct the Python call that created a node from its innermost call frame."""
    if not node.call_stack:
        return f"# {node.name} [{node.id}]: no call frame recorded"

    def generate_clip_names() -> Generator[str]:
        yield "clip"
        for n in count(2):
            yield f"clip{n}"

    frame = node.call_stack[0]
    clip_names = generate_clip_names()
    positional = list[str]()
    keyword = list[str]()

    for key, value in frame.to_dict()["arguments"].items():
        if is_clip_reference(value):
            positional.append(next(clip_names))
        elif isinstance(value, list) and value and all(map(is_clip_reference, value)):
            names = ", ".join(islice(clip_names, len(value)))
            keyword.append(f"{key}=[{names}]")
        else:
            keyword.append(f"{key}={format_python_value(value)}")

    clip_names.close()

    args = ", ".join([*positional, *keyword])
    return f"core.{frame.plugin_ns}.{frame.function}({args})"


def build_node_report(node: vsgraph.Node[Any], *, is_root: bool) -> str:
    """Build a plain-text report of everything recorded for a node."""
    match node.media_info:
        case vsgraph.VideoMediaInfo() as vi:
            media_line = (
                f"Media: {vi.format.name} {vi.width}x{vi.height} @ "
                f"{vi.fps_num}/{vi.fps_den} fps, {vi.num_frames} frames"
            )
        case vsgraph.AudioMediaInfo() as ai:
            media_line = (
                f"Media: {ai.format.name} {ai.sample_rate} Hz, "  # no fmt
                f"{ai.num_samples} samples, {ai.num_frames} frames"
            )
        case other:
            media_line = f"Media: {other}"

    lines = [
        f"{node.name} [{node.id}]",
        f"Mode: {node.filter_mode.name}",
        f"Time: {format_timing(node.processing_time_ns)}",
        media_line,
        "Output root: yes" if is_root else "Output root: no",
        "",
        "Call stack:",
    ]

    if not node.call_stack:
        lines.append("  (none recorded)")
        return "\n".join(lines)

    for frame in node.call_stack:
        lines.append(f"  [{frame.level}] {frame.plugin_ns}.{frame.function} ({frame.plugin_id})")
        for key, value in frame.to_dict()["arguments"].items():
            lines.append(f"      {key} = {format_python_value(value)}")

    return "\n".join(lines)


def is_clip_reference(value: Any) -> bool:
    if isinstance(value, vsgraph.NodeRef):
        return True
    if isinstance(value, dict):
        return value.get("type") in ("vnode", "anode")
    return False
