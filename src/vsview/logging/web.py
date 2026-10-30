from __future__ import annotations

import base64
import json
import re
import time
from logging import Logger, LogRecord, getLogger
from pathlib import Path
from typing import Any, NamedTuple

B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
SEARCH_ROOTS = [
    WORKSPACE_ROOT / "src/workspaces/editor",
    WORKSPACE_ROOT / "src/plugins/graph",
    WORKSPACE_ROOT / "src/shared/ts",
    WORKSPACE_ROOT,
]

SOURCEMAP_CACHE: dict[str, tuple[CachedMap | None, float]] = {}
CACHE_TTL = 2.0


class CachedMap(NamedTuple):
    mappings: list[list[Segment]]
    sources: list[str]


class Segment(NamedTuple):
    gen_col: int
    src_idx: int
    src_line: int
    src_col: int


def setup_web_logger(name: str) -> Logger:
    logger = getLogger(name)

    for h in logger.handlers[:]:
        logger.removeHandler(h)
    for f in logger.filters[:]:
        logger.removeFilter(f)

    logger.addFilter(context_filter)
    return logger


def context_filter(record: LogRecord) -> bool:
    source: str | None = getattr(record, "js_source", None)
    line: int | None = getattr(record, "js_lineno", None)

    if source and line:
        source, line = resolve_js_source_location(source, line)

        found_path: Path | None = None
        for root in SEARCH_ROOTS:
            cand = root / source
            if cand.is_file():
                found_path = cand
                break

        record.pathname = (found_path or (WORKSPACE_ROOT / source)).as_posix()
        record.filename = source
        record.module = source
        record.lineno = line

    return True


def resolve_sourcemap_location(source_id: str, line_number: int) -> tuple[str, int] | None:
    """Resolve an HTTP/HTTPS dev-server URL and 1-based line number to (source_filename, 1-based line)."""
    if not source_id.startswith(("http://", "https://")):
        return None

    import urllib.parse

    path = urllib.parse.urlsplit(source_id).path
    if "/node_modules/" in path or path.startswith(("/@vite/", "/@id/")):
        return None

    now = time.monotonic()
    if (entry := SOURCEMAP_CACHE.get(source_id)) and (now - entry[1] < CACHE_TTL):
        cached_map = entry[0]
    else:
        cached_map = _fetch_sourcemap(source_id)
        SOURCEMAP_CACHE[source_id] = (cached_map, now)

    if not cached_map:
        return None

    line0 = line_number - 1
    if not (0 <= line0 < len(cached_map.mappings)):
        return None

    if not (line_segs := next((segs for segs in reversed(cached_map.mappings[: line0 + 1]) if segs), None)):
        return None

    best = line_segs[0]
    raw_source = cached_map.sources[best.src_idx] if 0 <= best.src_idx < len(cached_map.sources) else "unknown"
    return _display_name(raw_source), best.src_line + 1


def resolve_js_source_location(source_id: str, line_number: int) -> tuple[str, int]:
    """Resolve a JavaScript console source ID and line number to clean display values."""
    if source_id.startswith(("http://", "https://")) and (
        resolved := resolve_sourcemap_location(source_id, line_number)
    ):
        return resolved

    clean_name = _display_name(source_id)
    return clean_name or source_id, line_number


def _display_name(src: str) -> str:
    p = src.replace("\\", "/")

    if (idx := p.rfind("/src/")) != -1:
        return p[idx + 1 :]

    if p.startswith("src/"):
        return p

    import urllib.parse

    return Path(urllib.parse.urlsplit(p).path).name


def _decode_mappings(mappings: str) -> list[list[Segment]]:
    """Returns, per generated line, a list of (gen_col, src_idx, src_line, src_col)."""
    lines: list[list[Segment]] = []
    src, sl, sc = 0, 0, 0
    for line in mappings.split(";"):
        gc = 0
        segs: list[Segment] = []
        for seg in line.split(","):
            if not seg or not (f := _vlq(seg)):
                continue
            gc += f[0]
            if len(f) >= 4:
                src += f[1]
                sl += f[2]
                sc += f[3]
                segs.append(Segment(gc, src, sl, sc))
        lines.append(segs)
    return lines


def _vlq(seg: str) -> list[int]:
    out = list[int]()
    shift = val = 0
    for ch in seg:
        d = B64[ch]
        val += (d & 31) << shift
        if d & 32:
            shift += 5
        else:
            out.append(-(val >> 1) if val & 1 else val >> 1)
            shift = val = 0
    return out


def _fetch_sourcemap(url: str) -> CachedMap | None:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    try:
        req = Request(url, headers={"User-Agent": "VSView-Dev"})
        with urlopen(req, timeout=0.5) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    match = re.search(r"/[/*]# sourceMappingURL=data:application/json[^,]*;base64,([A-Za-z0-9+/=]+)", content)
    if not match:
        return None

    try:
        raw_json = base64.b64decode(match.group(1)).decode("utf-8")
        data: dict[str, Any] = json.loads(raw_json)
        return CachedMap(
            mappings=_decode_mappings(data.get("mappings", "")),
            sources=data.get("sources", []),
        )
    except (json.JSONDecodeError, ValueError, KeyError, IndexError):
        return None
