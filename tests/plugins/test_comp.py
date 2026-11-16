from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import niquests
import niquests.cookies
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot
from vsview_comp.models import ComparisonImage, ComparisonSource, SlowPicsSources, TMDBTitle, TMDBTitleData
from vsview_comp.plugin import CompPlugin
from vsview_comp.ui import ProbabilityCurveWidget, TMDBListPopup
from vsview_comp.utils import LogNiquestsErrors, UploadError

from tests.harness import HarnessPluginController
from vsview.vsenv.loop import QtEventLoop

type Route = Callable[[str, str], FakeResponse]


pytestmark = [pytest.mark.unit, pytest.mark.qt]


class FakeAsyncSession:
    def __init__(self, route: Route, requests: list[tuple[str, str]]) -> None:
        self.route = route
        self.requests = requests
        self.cookies = niquests.cookies.RequestsCookieJar()
        self.headers = dict[str, str]()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_t: type[BaseException] | None, exc_val: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(("GET", url))
        return self.route("GET", url)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(("POST", url))
        return self.route("POST", url)

    @classmethod
    def install(cls, monkeypatch: pytest.MonkeyPatch, route: Route) -> list[tuple[str, str]]:
        requests = list[tuple[str, str]]()

        def factory(*args: Any, **kwargs: Any) -> Self:
            return cls(route, requests)

        monkeypatch.setattr(niquests, "AsyncSession", factory)
        monkeypatch.setattr(niquests, "Session", factory)
        return requests


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(body)
        self._body = body

    def raise_for_status(self) -> Self:
        if self.status_code >= 400:
            raise niquests.HTTPError(f"HTTP {self.status_code}")
        return self

    def json(self) -> Any:
        return self._body


@pytest.fixture
def comp_plugin(qt_event_loop: QtEventLoop, plugin_controller: HarnessPluginController) -> CompPlugin:
    return plugin_controller.create_plugin(CompPlugin)


def slowpics_route(image_status: int) -> Route:
    def route(method: str, url: str) -> FakeResponse:
        if url == "/comparison":
            return FakeResponse(200, "<html></html>")
        if url == "/upload/collection":
            return FakeResponse(
                200, {"collectionUuid": "coll", "key": "KEY", "images": [["img-0"]], "completeImageUuids": []}
            )
        if url.startswith("/upload/image/"):
            return FakeResponse(image_status, None, {"Retry-After": "0"})
        raise AssertionError(f"unexpected request {method} {url}")

    return route


def one_image_source(tmp_path: Path) -> SlowPicsSources:
    image = tmp_path / "05.png"
    image.write_bytes(b"png")
    return SlowPicsSources(
        collection_name="Test",
        sources=[ComparisonSource("Source", [ComparisonImage(image, "I", 5, "00:00:00.208")])],
        public=False,
        nsfw=False,
        tmdb_id=None,
        remove_after=0,
        tags=[],
    )


def ignore_progress(**kwargs: Any) -> None:
    return None


def test_upload_fails_when_an_image_is_rate_limited_on_every_attempt(
    comp_plugin: CompPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = FakeAsyncSession.install(monkeypatch, slowpics_route(429))

    fut = comp_plugin.slowpics_worker.upload(src=one_image_source(tmp_path), cookies={}, progress_cb=ignore_progress)

    with pytest.raises(ExceptionGroup) as info:
        fut.result(timeout=10)
    assert info.group_contains(UploadError, match=r"^Image 05\.png was rate limited on every attempt$")
    assert requests.count(("POST", "/upload/image/img-0")) == 5


def test_upload_succeeds_when_the_image_is_accepted(
    comp_plugin: CompPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = FakeAsyncSession.install(monkeypatch, slowpics_route(200))

    fut = comp_plugin.slowpics_worker.upload(src=one_image_source(tmp_path), cookies={}, progress_cb=ignore_progress)

    assert fut.result(timeout=10) == "https://slow.pics/c/KEY"
    assert requests.count(("POST", "/upload/image/img-0")) == 1


def test_tmdb_tooltip_escapes_server_text(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    popup = TMDBListPopup(host, QLineEdit(host))
    data = TMDBTitleData(id=1, name="Tom & <i>Jerry</i>", overview='<img src="file://server/share/a.png">')

    popup.show_results([TMDBTitle(data=data, media_type="tv")])
    tooltip = popup.item(0).toolTip()

    assert "Tom &amp; &lt;i&gt;Jerry&lt;/i&gt; (0000) [TV]" in tooltip
    assert "&lt;img src=&#34;file://server/share/a.png&#34;&gt;" in tooltip
    assert "<img" not in tooltip
    assert tooltip.lstrip().startswith("<div")


def test_tag_chips_show_labels_as_plain_text(comp_plugin: CompPlugin) -> None:
    comp_plugin.tags.add_tag("bold", "<b>Bold</b>")

    labels = [label for label in comp_plugin.tags.findChildren(QLabel) if label.text() == "<b>Bold</b>"]

    assert [label.textFormat() for label in labels] == [Qt.TextFormat.PlainText]


# P1-12
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timed out"), ["TMDB search failed: timed out"]),
        (niquests.HTTPError("HTTP 500"), ["TMDB search failed: HTTP 500"]),
        (KeyError("label"), ["TMDB search failed: 'label'"]),
        (CancelledError(), []),
        (asyncio.CancelledError(), []),
    ],
)
def test_log_niquests_errors_logs_every_failure_except_cancellation(
    error: BaseException, expected: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(type(error)), LogNiquestsErrors("TMDB search"):
        raise error

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == expected


def tmdb_route(numeric_status: int) -> Route:
    def route(method: str, url: str) -> FakeResponse:
        if url in ("/search/tv", "/search/movie"):
            return FakeResponse(200, {"results": []})
        if url in ("/genre/tv/list", "/genre/movie/list"):
            return FakeResponse(200, {"genres": []})
        if url == "/tv/1917":
            return FakeResponse(404, {"success": False, "status_code": 34, "status_message": "Not found."})
        if url == "/movie/1917":
            return FakeResponse(numeric_status, {"id": 530915, "title": "1917", "release_date": "2019-12-25"})
        raise AssertionError(f"unexpected request {method} {url}")

    return route


def test_tmdb_numeric_query_skips_not_found_lookups(
    comp_plugin: CompPlugin, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    FakeAsyncSession.install(monkeypatch, tmdb_route(200))

    titles = comp_plugin.tmdb_worker.search("1917").result(timeout=10)

    assert [(t.media_type, t.id, t.name) for t in titles] == [("movie", "530915", "1917")]
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("query", ["\u00b2", "\u216b"])
def test_tmdb_search_sends_only_decimal_queries_as_ids(
    comp_plugin: CompPlugin, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    requests = FakeAsyncSession.install(monkeypatch, tmdb_route(200))

    comp_plugin.tmdb_worker.search(query).result(timeout=10)

    assert sorted(url for _, url in requests) == ["/genre/movie/list", "/genre/tv/list", "/search/movie", "/search/tv"]


@pytest.mark.parametrize("key", [Qt.Key.Key_Delete, Qt.Key.Key_Backspace])
def test_curve_editor_key_removes_the_hovered_point(qtbot: QtBot, key: Qt.Key) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    widget = ProbabilityCurveWidget(host, 0, 100)
    widget.resize(400, 300)
    host.show()
    qtbot.waitExposed(host)
    widget.points = [QPointF(0.0, 1.0), QPointF(0.5, 2.0), QPointF(1.0, 1.0)]

    # Qt drops a move to the position the cursor already has, so leave the point first.
    qtbot.mouseMove(widget, QPoint(1, 1))
    qtbot.mouseMove(widget, widget.to_pixels(QPointF(0.5, 2.0)).toPoint())
    with qtbot.waitSignal(widget.curveChanged, timeout=1000):
        qtbot.keyClick(widget, key)

    assert [p.toTuple() for p in widget.points] == [(0.0, 1.0), (1.0, 1.0)]
