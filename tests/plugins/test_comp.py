from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import niquests
import niquests.cookies
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot
from vsview_comp.models import ComparisonImage, ComparisonSource, SlowPicsSources, TMDBTitle, TMDBTitleData
from vsview_comp.plugin import CompPlugin
from vsview_comp.ui import TMDBListPopup
from vsview_comp.utils import UploadError

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


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # With PySide6 imported on CPython 3.15, concurrent.futures.ThreadPoolExecutor stays an unresolved lazy import,
    # so the default asyncio executor cannot be created.
    async def run_inline[**P, R](func: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", run_inline)


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
