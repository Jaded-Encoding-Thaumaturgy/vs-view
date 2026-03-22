from __future__ import annotations

import asyncio
import os
import re
import threading
from collections.abc import Awaitable, Iterator, Mapping, Sequence
from concurrent.futures import Future, wait
from contextlib import aclosing
from datetime import UTC, datetime
from itertools import chain
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple
from uuid import uuid4

import anyio
import httpx
from jetpytools import cachedproperty, ndigits
from pathvalidate import sanitize_filepath
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage
from vapoursynth import GRAY8, RGB24, VideoNode
from vstools import clip_data_gather, core, get_prop, remap_frames

from vsview.api import PluginAPI, PluginSecrets, Time, run_in_background

from ._metadata import COOKIE_KEY, LOGIN_CONTEXT
from .models import ComparisonSource, TMDBPayload, TMDBTitle, TMDBTitleData
from .ui import FrameSourceProvider, ProgressBar
from .utils import LogHTTPXErrors, demote_httpx_logs, get_random_number_interval, get_slowpics_headers

if TYPE_CHECKING:
    from .plugin import CompPlugin

logger = getLogger(__name__)


class ExtractFramesWorker:
    def __init__(self, api: PluginAPI, parent: CompPlugin) -> None:
        self.api = api
        self.progress_bar = parent.progress_bar
        self.data = parent.frames_list.get_data()
        self.included_outputs = parent.outputs_dropdown.included_outputs

        if not (storage := self.api.get_local_storage(parent)):
            raise NotImplementedError
        self.storage = storage

    @run_in_background(name="ExtractFrames")
    def run(self) -> list[tuple[int, Path]]:
        self.progress_bar.update_progress(
            range=(0, len(self.data) * len(self.included_outputs)),
            fmt="Extracting frames %v / %m",
            value=0,
        )

        path = self.storage / datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H-%M-%S")
        workers = list[Future[None]]()
        images_paths = list[tuple[int, Path]]()

        with self.api.vs_context():
            is_fpng_available = hasattr(core, "fpng")

            for output in self.api.voutputs:
                if output.vs_index not in self.included_outputs:
                    continue

                images_path = path / f"({output.vs_index}) ({output.vs_name})"
                images_path = sanitize_filepath(images_path, replacement_text="_")
                images_path.mkdir(parents=True, exist_ok=True)
                images_paths.append((output.vs_index, images_path))

                clip = self.api.packer.to_rgb_planar(output.vs_output.clip, format=RGB24)
                frames = [output.time_to_frame(t) for t, *_ in self.data]
                clip_image_path = images_path / f"%0{ndigits(max(frames))}d.png"

                if is_fpng_available:
                    f = self._fpng_extract(clip, clip_image_path, frames)
                else:
                    f = self._qt_extract(clip, clip_image_path, frames)

                workers.append(f)

            # Wait for workers to finish extracting
            wait(workers)

        return images_paths

    @run_in_background(name="ExtractFPNG")
    def _fpng_extract(self, clip: VideoNode, path: Path, frames: Sequence[int]) -> None:
        # TODO: Maybe add alpha support?
        with self.api.vs_context():
            # 1 - slow compression (smaller output file)
            clip = clip.fpng.Write(filename=str(path), compression=1)
            remapped = remap_frames(clip, frames)

            with open(os.devnull, "wb") as sink:
                remapped.output(sink, progress_update=lambda *_: self.progress_bar.update_progress(increment=1))

    @run_in_background(name="ExtractQt")
    def _qt_extract(self, clip: VideoNode, path: Path, frames: Sequence[int]) -> None:
        with self.api.vs_context():
            clip = self.api.packer.to_rgb_packed(clip)
            remapped = remap_frames(clip, frames)

            sema = threading.Semaphore(QThreadPool.globalInstance().maxThreadCount() // 2)
            workers = list[Future[None]]()

            for n, vs_frame in zip(frames, remapped.frames(close=True)):
                sema.acquire()
                qimage = self.api.packer.frame_to_qimage(vs_frame, format=QImage.Format.Format_RGB32).copy()
                f = self._qt_save(qimage, path.with_stem(path.stem % n))
                f.add_done_callback(lambda _: sema.release())
                workers.append(f)

            wait(workers)

    @run_in_background(name="QtSave")
    def _qt_save(self, qimage: QImage, path: Path) -> None:
        qimage.save(str(path), "PNG", 75)  # type: ignore[call-overload]
        self.progress_bar.update_progress(increment=1)


class SelectFrameWorker:
    ALLOWED_FRAME_SEARCHES = 150

    def __init__(self, api: PluginAPI, parent: CompPlugin) -> None:
        self.api = api
        self.progress_bar = parent.progress_bar

        self.start = Time.from_qtime(parent.time_edit_start.time())
        self.end = Time.from_qtime(parent.time_edit_end.time())
        self.dark = parent.dark_frame_count.value()
        self.light = parent.light_frame_count.value()
        self.normal = parent.random_frame_count.value() - self.dark - self.light

        # Existing frames to avoid duplicates
        v = self.api.current_voutput
        self.checked = [int(v.time_to_frame(t)) for t, *_ in parent.frames_list.get_data()]

        # Picture types
        self.pict_types = list[str]()
        if parent.pict_type_i_cb.isChecked():
            self.pict_types.append("I")
        if parent.pict_type_p_cb.isChecked():
            self.pict_types.append("P")
        if parent.pict_type_b_cb.isChecked():
            self.pict_types.append("B")

        self.should_check_pict = len(self.pict_types) < 3 and parent.pict_types_supported
        self.should_check_combed = not parent.combed_cb.isChecked()

    @run_in_background(name="SelectFrames")
    def run(self) -> list[tuple[Time, FrameSourceProvider]]:
        with self.api.vs_context():
            return self.get()

    def get(self) -> list[tuple[Time, FrameSourceProvider]]:
        found_times = list[tuple[Time, FrameSourceProvider]]()

        if self.normal > 0:
            found_times.extend((t, FrameSourceProvider.RANDOM) for t in self._get_normal_frames())

        if self.dark > 0 or self.light > 0:
            found_times.extend(self._get_light_dark_frames())

        return sorted(set(found_times))

    def _get_normal_frames(self) -> list[Time]:
        v = self.api.current_voutput
        start_frame, end_frame = v.time_to_frame(self.start), v.time_to_frame(self.end)

        self.progress_bar.update_progress(range=(0, self.normal), fmt="Selecting frames %v / %m", value=0)

        random_frames = list[Time]()
        base_clip = core.std.BlankClip(width=1, height=1, format=GRAY8, length=len(self.api.voutputs), keep=True)
        other_clips = [source.vs_output.clip for source in self.api.voutputs]

        while len(random_frames) < self.normal:
            for _ in range(self.ALLOWED_FRAME_SEARCHES):
                rnum = get_random_number_interval(
                    start_frame,
                    end_frame,
                    self.normal,
                    len(random_frames),
                    self.checked,
                )
                self.checked.append(rnum)

                is_valid = True
                if self.should_check_pict or self.should_check_combed:
                    # Check frame properties across all outputs
                    node_frames = core.std.FrameEval(
                        base_clip,
                        lambda n, r=rnum: base_clip.std.CopyFrameProps(
                            other_clips[n][r], props=["_PictType", "_Combed"]
                        ),
                    )

                    for f in node_frames.frames(close=True):
                        is_pict_type_not_selected = (
                            self.should_check_pict
                            and get_prop(f, "_PictType", str, default="", func="__vsview__") not in self.pict_types
                        )
                        is_combed = (
                            self.should_check_combed
                            and get_prop(f, "_Combed", int, default=0, func="__vsview__")  # No format
                        )

                        if is_pict_type_not_selected or is_combed:
                            is_valid = False
                            break

                if is_valid:
                    random_frames.append(v.frame_to_time(rnum))
                    self.progress_bar.update_progress(value=len(random_frames))
                    break
            else:
                logger.warning(
                    "Max attempts reached searching for random frames. Found %s/%s",
                    len(random_frames),
                    self.normal,
                )
                break

        return random_frames

    def _get_light_dark_frames(self) -> Iterator[tuple[Time, FrameSourceProvider]]:
        v = self.api.current_voutput
        start, end = v.time_to_frame(self.start), v.time_to_frame(self.end)

        # Sample frames for brightness analysis
        step = max(1, (end - start) // (self.ALLOWED_FRAME_SEARCHES * 3))
        frames_to_check = range(start, end, step)

        self.progress_bar.update_progress(
            range=(0, len(frames_to_check)), fmt="Checking frames light levels %v / %m", value=0
        )

        checked_count = 0

        def _progress(*_: Any) -> None:
            nonlocal checked_count
            checked_count += 1
            self.progress_bar.update_progress(value=checked_count)

        decimated = remap_frames(v.vs_output.clip, frames_to_check).std.PlaneStats()
        avg_levels = clip_data_gather(
            decimated,
            _progress,
            lambda n, f: get_prop(f, "PlaneStatsAverage", float, default=0, func=self._get_light_dark_frames),
        )

        # Pair levels with frames and sort by brightness
        sorted_frames = [f for _, f in sorted(zip(avg_levels, frames_to_check))]

        dark = sorted_frames[: self.dark] if self.dark else []
        light = sorted_frames[-self.light :] if self.light else []

        return chain(
            ((v.frame_to_time(f), FrameSourceProvider.RANDOM_DARK) for f in dark),
            ((v.frame_to_time(f), FrameSourceProvider.RANDOM_LIGHT) for f in light),
        )


class TMDBWorker:
    BASE_URL = "https://api.themoviedb.org/3"
    BASE_PARAMS: Mapping[str, str] = {"include_adult": "false", "language": "en-US"}
    API_KEY_PATH = Path(__file__).parent / "tmdb_api_key.txt"

    def __init__(self) -> None:
        self._movie_genres = dict[int, str]()
        self._tv_genres = dict[int, str]()

    @cachedproperty
    def api_key(self) -> str:
        return self.API_KEY_PATH.read_text().strip()

    @run_in_background(name="TMDBSearch")
    @demote_httpx_logs
    async def search(self, query: str) -> list[TMDBTitle]:
        titles = list[TMDBTitle]()
        search_params = {**self.BASE_PARAMS, "query": query}
        api_headers = {"Authorization": f"Bearer {self.api_key}"}

        async with (
            httpx.AsyncClient(base_url=self.BASE_URL, timeout=10, headers=api_headers) as client,
            LogHTTPXErrors("TMDB search"),
        ):
            await self._ensure_genres_loaded(client)

            tv_task = client.get("/search/tv", params=search_params)
            movie_task = client.get("/search/movie", params=search_params)

            tv_resp, movie_resp = await asyncio.gather(tv_task, movie_task)

            title_tasks = list[TMDBTitle]()

            tv_data = TMDBPayload.validate_logged(tv_resp.raise_for_status().json(), "TMDB /search/tv")
            if tv_data:
                title_tasks.extend(self._create_title(item, "tv") for item in tv_data.results)

            movie_data = TMDBPayload.validate_logged(movie_resp.raise_for_status().json(), "TMDB /search/movie")
            if movie_data:
                title_tasks.extend(self._create_title(item, "movie") for item in movie_data.results)

            titles.extend(title_tasks)

            if query.isnumeric():
                tv_id_task = client.get(f"/tv/{query}", params=self.BASE_PARAMS)
                movie_id_task = client.get(f"/movie/{query}", params=self.BASE_PARAMS)

                responses = await asyncio.gather(tv_id_task, movie_id_task, return_exceptions=True)

                for res, genre_type in zip(responses, ["tv", "movie"]):
                    if isinstance(res, httpx.Response) and res.status_code == 200:
                        item = TMDBTitleData.validate_logged(res.json(), f"TMDB /{genre_type}/{query}")
                        if item:
                            titles.append(self._create_title(item, genre_type))  # type: ignore[arg-type]

        return titles

    async def _ensure_genres_loaded(self, client: httpx.AsyncClient) -> None:
        if not self._tv_genres or not self._movie_genres:
            tv_req = client.get("/genre/tv/list")
            movie_req = client.get("/genre/movie/list")

            tv_res, movie_res = await asyncio.gather(tv_req, movie_req, return_exceptions=True)

            if isinstance(tv_res, httpx.Response) and tv_res.status_code == 200 and not self._tv_genres:
                data = TMDBPayload.validate_logged(tv_res.json(), "TMDB /genre/tv/list")
                if data:
                    self._tv_genres = {g.id: g.name for g in data.genres}

            if isinstance(movie_res, httpx.Response) and movie_res.status_code == 200 and not self._movie_genres:
                data = TMDBPayload.validate_logged(movie_res.json(), "TMDB /genre/movie/list")
                if data:
                    self._movie_genres = {g.id: g.name for g in data.genres}

    def _create_title(self, item: TMDBTitleData, media_type: Literal["tv", "movie"]) -> TMDBTitle:
        genres_map = self._tv_genres if media_type == "tv" else self._movie_genres
        mapped_genres = [genres_map[g_id] for g_id in item.genre_ids if g_id in genres_map]
        inline_genres = [g.name for g in item.genres if g.name]
        genres = inline_genres or mapped_genres
        return TMDBTitle(data=item, media_type=media_type, genres=genres)


class Tag(NamedTuple):
    value: str
    label: str


class SlowPicsWorker:
    BASE_URL = "https://slow.pics"
    MAX_CONCURRENT_REQUESTS = 6

    def __init__(self, api: PluginAPI, secrets: PluginSecrets, progress_bar: ProgressBar) -> None:
        self.api = api
        self.secrets = secrets
        self.progress_bar = progress_bar
        self.browser_id = str(uuid4())
        self.headers = get_slowpics_headers()
        self.limits = httpx.Limits(
            max_connections=self.MAX_CONCURRENT_REQUESTS,
            max_keepalive_connections=self.MAX_CONCURRENT_REQUESTS,
        )
        self.sema = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

    @run_in_background(name="SlowPicsTags")
    @demote_httpx_logs
    def get_tags(self) -> list[Tag]:
        with (
            httpx.Client(base_url=self.BASE_URL, headers=self.headers) as client,
            LogHTTPXErrors("Slowpics Tags"),
        ):
            tags = client.get("/api/tags").raise_for_status().json()
            return [Tag(tag["value"], tag["label"].strip()) for tag in tags]

    @run_in_background(name="SlowPicsLogin")
    @demote_httpx_logs
    def get_cookies(self) -> dict[str, str]:
        if cookies := self.secrets.get_json(COOKIE_KEY, COOKIE_KEY):
            return cookies

        if not (credentials := self.secrets.get_credential(LOGIN_CONTEXT)):
            return {}

        try:
            with httpx.Client(base_url=self.BASE_URL, headers=self.headers, timeout=20) as client:
                # Grab initial XSRF token
                client.get("/comparison").raise_for_status()
                client.headers.update({"X-XSRF-TOKEN": client.cookies.get("XSRF-TOKEN") or ""})

                # Extract CSRF token
                login_page = client.get("/login").raise_for_status()

                csrf_match = re.search(
                    r'<input type="hidden" name="_csrf" value="([a-zA-Z0-9-_]+)"\/>', login_page.text
                )
                if not csrf_match:
                    logger.error("No CSRF found; Failed to login.")
                    return {}

                # Submit login payload
                data = {
                    "username": credentials.username,
                    "password": credentials.password,
                    "_csrf": csrf_match.group(1),
                    "remember-me": "on",
                }
                client.post("/login", data=data, follow_redirects=True).raise_for_status()

                # Save and return cookies
                cookies = self._cookies_jar(client.cookies)
                self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, cookies)

                return cookies

        except httpx.HTTPError as e:
            logger.error("Login sequence failed: %s", e)
            logger.debug("Full traceback", exc_info=True)
            return {}

    @run_in_background(name="SlowPicsUpload")
    @demote_httpx_logs
    async def upload(
        self,
        *,
        collection_name: str,
        sources: list[ComparisonSource],
        public: bool,
        nsfw: bool,
        tmdb_id: str | None,
        remove_after: int,
        tags: list[str],
        cookies: dict[str, str],
    ) -> str:
        is_comparison = len(sources) > 1
        total_images = sum(len(images) for _, images in sources)

        async with (
            httpx.AsyncClient(base_url=self.BASE_URL, limits=self.limits, headers=self.headers, timeout=20) as client,
            LogHTTPXErrors("Slowpics Upload"),
        ):
            await self._setup_client(client, cookies)

            # Build comparison/collection payload
            comp_upload = dict[str, str]()

            for i, (source_name, images) in enumerate(sources):
                for j, image in enumerate(images):
                    time_str = f"{image.timestamp} / {image.frame_no}"

                    if is_comparison:
                        comp_upload[f"comparisons[{j}].name"] = time_str
                        comp_upload[f"comparisons[{j}].imageNames[{i}]"] = (
                            f"{source_name}{f' - ({image.pict_type} Frame)' if image.pict_type != '?' else ''}"
                        )
                    else:
                        comp_upload[f"imageNames[{j}]"] = f"{time_str} - {source_name}"

            tag_data = {f"tags[{i}]": tag for i, tag in enumerate(tags)} if public else {}

            comp_info = {
                "collectionName": collection_name,
                "hentai": str(nsfw).lower(),
                "optimizeImages": "true",
                "browserId": self.browser_id,
                "public": str(public).lower(),
            }

            if tmdb_id:
                comp_info["tmdbId"] = tmdb_id
            if remove_after >= 1:
                comp_info["removeAfter"] = str(remove_after)

            endpoint = f"/upload/{'comparison' if is_comparison else 'collection'}"
            comp_data = (await client.post(endpoint, data=comp_upload | tag_data | comp_info)).raise_for_status().json()

            collection_uuid = comp_data["collectionUuid"]
            key = comp_data["key"]
            image_ids: list[list[str]] = comp_data["images"]

            logger.debug("Starting upload of: https://slow.pics/c/%s", key)

            # Upload images concurrently
            self.progress_bar.update_progress(range=(0, total_images), fmt="Uploading images %v / %m", value=0)
            reqs = list[Awaitable[None]]()

            for i, (_, images) in enumerate(sources):
                for j, (image_path, *_) in enumerate(images):
                    image_uuid = image_ids[j][i] if is_comparison else image_ids[0][j]
                    reqs.append(self._upload_image(client, collection_uuid, image_uuid, image_path))

            for i, coro in enumerate(asyncio.as_completed(reqs), start=1):
                await coro
                self.progress_bar.update_progress(value=i)

            self.progress_bar.update_progress(fmt="Finished uploading %v images")

            # Refresh cookies
            self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, self._cookies_jar(client.cookies))

            return f"https://slow.pics/c/{key}"

    async def _setup_client(self, client: httpx.AsyncClient, cookies: dict[str, str]) -> None:
        for key, value in cookies.items():
            client.cookies.set(key, value)

        async with aclosing(await client.get("/comparison")) as homepage:
            homepage.raise_for_status()
            client.headers.update({"X-XSRF-TOKEN": client.cookies.get("XSRF-TOKEN") or ""})

            if cookies:
                if 'id="logoutBtn"' in homepage.text:
                    logger.debug("Cookies not stale, logged in.")
                    self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, self._cookies_jar(client.cookies))
                else:
                    logger.warning("Cookies have expired, uploading as anonymous")

    async def _upload_image(
        self,
        client: httpx.AsyncClient,
        collection: str,
        image_uuid: str,
        image_path: Path,
    ) -> None:
        url = f"/upload/image/{image_uuid}"
        data = {"collectionUuid": collection, "imageUuid": image_uuid, "browserId": self.browser_id}

        # Handle 429 "Too many requests"
        for retry in range(5):
            try:
                async with self.sema:
                    response = await client.post(
                        url,
                        data=data,
                        files={"file": (image_path.name, await anyio.Path(image_path).read_bytes(), "image/png")},
                    )

                if response.status_code == 429:
                    wait_time = int(response.headers.get("Retry-After", (retry + 1) * 2))
                    logger.warning("Rate limited for %s. Waiting %ds...", image_path.name, wait_time)
                    await asyncio.sleep(wait_time)
                    continue

                response.raise_for_status()
                return
            except Exception as e:
                if retry == 4:
                    raise
                logger.error("Error uploading %s (attempt %d) '%s'", image_path.name, retry + 1, e)
                logger.debug("Traceback:", exc_info=True)
                await asyncio.sleep((retry + 1) * 2)

    @staticmethod
    def _cookies_jar(cookies: httpx.Cookies) -> dict[str, str]:
        return {c.name: c.value or "" for c in cookies.jar}
