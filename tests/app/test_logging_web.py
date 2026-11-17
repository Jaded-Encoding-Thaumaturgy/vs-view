from __future__ import annotations

from urllib.error import URLError

import pytest
from pytest_mock import MockerFixture

from vsview.logging import web

pytestmark = [pytest.mark.unit]


def test_sourcemap_cache_ignores_the_hmr_query_string(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.setattr(web, "SOURCEMAP_CACHE", {})
    # The dev server is a network boundary; a refused connection caches "no map".
    urlopen = mocker.patch("urllib.request.urlopen", side_effect=URLError("refused"))

    for query in ("?t=1", "?t=2", "?t=3#frag"):
        assert web.resolve_sourcemap_location(f"http://localhost:5173/src/main.ts{query}", 1) is None

    assert urlopen.call_count == 1
    assert list(web.SOURCEMAP_CACHE) == ["http://localhost:5173/src/main.ts"]
