from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from fractions import Fraction
from logging import getLogger
from typing import Any, Final, Literal, NotRequired, Self, TypedDict

import vapoursynth as vs
import vsgraph

type Severity = Literal["warning", "info"]

logger = getLogger(__name__)

SEVERITY_ORDER: Final[Mapping[Severity, int]] = {"warning": 0, "info": 1}


class WarningCode(StrEnum):
    FRAME_STATE = (
        "framestate",
        "Frame-state serialization",
        "Serializes the entire frame lifecycle to one frame at a time, bypassing parallelism.",
    )
    UNORDERED = (
        "unordered",
        "Single-threaded execution (Unordered)",
        "Only one thread can execute the filter's getFrame at a time, with calls in arbitrary order.",
    )
    NO_FRAME_REUSE = (
        "no-frame-reuse",
        "No input frame reuse",
        "Requests each upstream frame at most once, disabling the upstream cache when it has a single consumer.",
    )
    STRICT_SPATIAL = (
        "strict-spatial",
        "Strict 1:1 frame requests (Strict spatial)",
        "Only requests input frame n for output frame n, disabling the upstream cache when it has a single consumer.",
    )
    FRAME_REUSE_LAST_ONLY = (
        "frame-reuse-last-only",
        "Last-frame reuse only",
        (
            "Requests each upstream frame at most once except the last frame, "
            "caching only the last frame when it has a single consumer."
        ),
    )

    wtitle: str
    description: str

    def __new__(cls, value: str, wtitle: str, description: str) -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.wtitle = wtitle
        obj.description = description
        return obj


class WarningGroup(TypedDict):
    code: WarningCode
    severity: Severity
    title: str
    description: str
    warnings: list[GraphWarning]
    nodeIds: list[str]


class GraphWarning(TypedDict):
    code: WarningCode
    severity: Severity
    nodeId: str
    edgeId: NotRequired[str]


class MediaTransitionGroup(TypedDict):
    signature: str
    fields: list[str]
    transitions: list[MediaTransition]
    targetNodeIds: list[str]


class MediaTransition(TypedDict):
    edgeId: str
    sourceId: str
    targetId: str
    delta: MediaDelta


class MediaDelta(TypedDict):
    fields: list[str]
    label: str


class NameCount(TypedDict):
    name: str
    count: int


def enrich_cytoscape_payload(graph: vsgraph.Graph[Any], payload: dict[str, Any]) -> dict[str, Any]:
    transitions = collect_media_transitions(graph)
    deltas_by_edge_id = {t["edgeId"]: t["delta"] for t in transitions}

    for edge_elem in payload.get("edges", ()):
        edge_data = edge_elem["data"]
        if (delta := deltas_by_edge_id.get(edge_data["id"])) is not None:
            edge_data["media_label"] = delta["label"]
            edge_data["media_fields"] = delta["fields"]

    warnings = collect_warnings(graph)

    payload["analysis"] = {
        "nodeCount": len(graph.nodes),
        "edgeCount": len(graph.edges),
        "depth": compute_depth(graph),
        "modes": count_by(node.filter_mode.name for node in graph.nodes),
        "patterns": count_by(edge.pattern.name for edge in graph.edges),
        "formats": count_by(node.media_info.format.name for node in graph.nodes),
        "warningCount": len(warnings),
        "warningGroups": group_warnings(warnings),
        "transitionCount": len(transitions),
        "transitionGroups": group_media_transitions(transitions),
    }

    return payload


def collect_media_transitions(graph: vsgraph.Graph[Any]) -> list[MediaTransition]:
    """Collect every edge of the graph whose endpoints disagree on media info."""
    by_id = graph.nodes_by_id
    transitions = list[MediaTransition]()

    for i, edge in enumerate(graph.edges):
        src_node = by_id.get(edge.source)
        dst_node = by_id.get(edge.target)

        if not src_node or not dst_node:
            logger.warning("Skipping edge %s -> %s: endpoint node missing from graph", edge.source, edge.target)
            continue

        if not (delta := describe_media_delta(src_node.media_info, dst_node.media_info)):
            continue

        transitions.append(
            MediaTransition(
                edgeId=format_edge_id(i, edge.source, edge.target),
                sourceId=edge.source,
                targetId=edge.target,
                delta=delta,
            )
        )

    return transitions


def format_edge_id(index: int, source: str, target: str) -> str:
    return f"e{index}_{source}_{target}"


def describe_media_delta(from_info: vsgraph.VideoMediaInfo, to_info: vsgraph.VideoMediaInfo) -> MediaDelta | None:
    """Describe how media properties changed along a single edge."""
    changes = list[tuple[str, str]]()

    def format_fps(fps: Fraction) -> str:
        """Format rational frame rate with up to 3 decimal places and stripped trailing zeros."""
        return "0" if math.isclose(fps, 0) else f"{fps.numerator / fps.denominator:.3f}".rstrip("0").rstrip(".")

    if from_info.format_id != to_info.format_id or from_info.format.name != to_info.format.name:
        changes.append(("format", f"{from_info.format.name}→{to_info.format.name}"))

    if (from_info.width, from_info.height) != (to_info.width, to_info.height):
        changes.append(("resolution", f"{from_info.width}x{from_info.height}→{to_info.width}x{to_info.height}"))

    if from_info.fps != to_info.fps:
        changes.append(("fps", f"{format_fps(from_info.fps)}→{format_fps(to_info.fps)} fps"))

    if from_info.num_frames != to_info.num_frames:
        changes.append(("frames", f"{from_info.num_frames}→{to_info.num_frames} frames"))

    if not changes:
        return None

    fields, labels = zip(*changes)
    return MediaDelta(fields=list(fields), label=", ".join(labels))


def collect_warnings(graph: vsgraph.Graph[Any]) -> list[GraphWarning]:
    unique = dict[tuple[str, str, str], GraphWarning]()

    for node in graph.nodes:
        if node.filter_mode is vs.FilterMode.FRAME_STATE:
            w = GraphWarning(code=WarningCode.FRAME_STATE, severity="warning", nodeId=node.id)
        elif node.filter_mode is vs.FilterMode.UNORDERED:
            w = GraphWarning(code=WarningCode.UNORDERED, severity="info", nodeId=node.id)
        else:
            continue
        unique.setdefault((w["code"], w["nodeId"], ""), w)

    for i, edge in enumerate(graph.edges):
        edge_id = format_edge_id(i, edge.source, edge.target)
        if edge.pattern is vsgraph.RequestPattern.NO_FRAME_REUSE:
            w = GraphWarning(code=WarningCode.NO_FRAME_REUSE, severity="warning", edgeId=edge_id, nodeId=edge.target)
        elif edge.pattern is vsgraph.RequestPattern.STRICT_SPATIAL:
            w = GraphWarning(code=WarningCode.STRICT_SPATIAL, severity="info", edgeId=edge_id, nodeId=edge.target)
        else:
            continue
        unique.setdefault((w["code"], w["nodeId"], w.get("edgeId", "")), w)

    return sorted(unique.values(), key=lambda w: SEVERITY_ORDER[w["severity"]])


def compute_depth(graph: vsgraph.Graph[Any]) -> int:
    """
    Longest dependency chain length, in nodes.

    Uses a Kahn-style traversal so cycles stop contributing without hanging.
    """
    ids = {node.id for node in graph.nodes}
    indegree = dict.fromkeys(ids, 0)
    adjacency: defaultdict[str, list[str]] = defaultdict(list)

    for edge in graph.edges:
        if edge.source in ids and edge.target in ids:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

    depth = dict.fromkeys(ids, 1)
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)

    while queue:
        node_id = queue.popleft()
        for nxt in adjacency[node_id]:
            depth[nxt] = max(depth[nxt], depth[node_id] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    return max(depth.values(), default=0)


def count_by(values: Iterable[str]) -> list[NameCount]:
    counts = Counter(values)
    return [NameCount(name=name, count=count) for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def group_warnings(warnings: Sequence[GraphWarning]) -> list[WarningGroup]:
    by_code: defaultdict[WarningCode, list[GraphWarning]] = defaultdict(list)
    for w in warnings:
        by_code[w["code"]].append(w)

    groups = list[WarningGroup]()
    for code, members in by_code.items():
        groups.append(
            WarningGroup(
                code=code,
                severity=members[0]["severity"],
                title=code.wtitle,
                description=code.description,
                warnings=members,
                nodeIds=list(dict.fromkeys(node_id for w in members if (node_id := w.get("nodeId")))),
            )
        )

    return sorted(groups, key=lambda g: (SEVERITY_ORDER[g["severity"]], -len(g["warnings"]), g["title"]))


def group_media_transitions(transitions: Iterable[MediaTransition]) -> list[MediaTransitionGroup]:
    """Group media transitions by their transition signature label."""
    by_signature: defaultdict[str, list[MediaTransition]] = defaultdict(list)
    for t in transitions:
        by_signature[t["delta"]["label"]].append(t)

    groups = [
        MediaTransitionGroup(
            signature=signature,
            fields=members[0]["delta"]["fields"],
            transitions=members,
            targetNodeIds=list(dict.fromkeys(t["targetId"] for t in members)),  # ordered de-dup
        )
        for signature, members in by_signature.items()
    ]

    return sorted(groups, key=lambda g: (-len(g["transitions"]), g["signature"]))
