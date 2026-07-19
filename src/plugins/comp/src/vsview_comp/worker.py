from __future__ import annotations

import asyncio
import hashlib
import random
import re
import threading
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import CancelledError, Future, wait
from contextlib import suppress
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from logging import DEBUG, getLogger
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import niquests
import niquests.cookies
from jetpytools import cachedproperty, clamp, ndigits
from pathvalidate import sanitize_filepath
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QImage
from vapoursynth import GRAY8, VideoNode
from vstools import DitherType, clip_data_gather, core, depth, get_prop, remap_frames, vs

from vsview.api import Packer, PluginAPI, PluginSecrets, PluginSettings, Time, run_in_background

from ._metadata import COOKIE_KEY, LOGIN_CONTEXT
from .models import SlowPicsSources, SlowPicsUploadResponse, TMDBPayload, TMDBTitle, TMDBTitleData
from .ui import FrameSourceProvider, ProgressBar
from .utils import (
    LogNiquestsErrors,
    UploadError,
    asymmetric_gaussian,
    build_cdf,
    get_probability_cdf,
    get_slowpics_headers,
)

if TYPE_CHECKING:
    from .plugin import CompPlugin, GlobalSettings

REV_CONF = niquests.RevocationConfiguration(niquests.RevocationStrategy.PREFER_CRL)

logger = getLogger(__name__)


class ExtractFramesWorker:
    def __init__(self, api: PluginAPI, parent: CompPlugin) -> None:
        self.api = api
        self.progress_bar = parent.progress_bar
        self.data = parent.frames_list.get_data()
        self.voutputs = parent.selected_voutputs
        self.packer = Packer(8)
        self._is_cancelled = False
        self._lock = threading.Lock()

        if not (storage := self.api.get_local_storage(parent)):
            raise NotImplementedError
        self.storage = storage

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._is_cancelled

    @run_in_background(name="ExtractFrames")
    def run(self) -> list[tuple[int, Path]]:
        with self.api.vs_context():
            return self._extract()

    def cancel(self) -> None:
        with self._lock:
            self._is_cancelled = True

    def _extract(self) -> list[tuple[int, Path]]:
        self.progress_bar.update_progress(
            range=(0, len(self.data) * len(self.voutputs)),
            fmt="Extracting frames %v / %m",
            value=0,
        )

        path = self.storage / datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H-%M-%S")
        workers = list[Future[None]]()
        images_paths = list[tuple[int, Path]]()

        try:
            dither_type = attrgetter("settings.view")(self.api)
        except AttributeError:
            dither_type = DitherType.RANDOM

        with self.api.vs_context():
            is_fpng_available = hasattr(core, "fpng")

            for output in self.voutputs:
                if self.is_cancelled:
                    raise CancelledError("Extract frames cancelled")
                images_path = path / f"({output.vs_index}) ({output.vs_name})"
                images_path = sanitize_filepath(images_path, replacement_text="_")
                images_path.mkdir(parents=True, exist_ok=True)
                images_paths.append((output.vs_index, images_path))

                clip = self.packer.to_rgb_planar(output.vs_output.clip)
                alpha = depth(a, 8, dither_type=dither_type) if (a := output.vs_output.alpha) else None
                frames = [output.time_to_frame(t) for t, *_ in self.data]
                clip_image_path = images_path / f"%0{ndigits(max(frames))}d.png"

                if is_fpng_available:
                    f = self._fpng_extract(clip, alpha, clip_image_path, frames)
                else:
                    f = self._qt_extract(clip, alpha, clip_image_path, frames)

                workers.append(f)

            # Wait for workers to finish extracting
            while not all(w.done() for w in workers):
                if self.is_cancelled:
                    raise CancelledError("Extract frames cancelled")
                wait(workers, timeout=0.1)

        return images_paths

    @run_in_background(name="ExtractFPNG")
    def _fpng_extract(self, clip: VideoNode, alpha: VideoNode | None, path: Path, frames: Sequence[int]) -> None:
        with self.api.vs_context():
            clip = clip.fpng.Write(filename=str(path), compression=1, alpha=alpha)
            remapped = remap_frames(clip, frames)

            for _ in remapped.frames(close=True):
                if self.is_cancelled:
                    raise CancelledError("Extract frames cancelled")
                self.progress_bar.update_progress(increment=1)

    @run_in_background(name="ExtractQt")
    def _qt_extract(self, clip: VideoNode, alpha: VideoNode | None, path: Path, frames: Sequence[int]) -> None:
        with self.api.vs_context():
            clip = self.packer.to_rgb_packed(clip, alpha)
            remapped = remap_frames(clip, frames)

            sema = threading.Semaphore(QThreadPool.globalInstance().maxThreadCount() // 2)
            workers = list[Future[None]]()

            for n, vs_frame in zip(frames, remapped.frames(close=True)):
                if self.is_cancelled:
                    break
                sema.acquire()
                qimage = self.packer.frame_to_qimage(vs_frame).copy()
                f = self._qt_save(qimage, path.with_stem(path.stem % n))
                f.add_done_callback(lambda _: sema.release())
                workers.append(f)

            wait(workers)

    @run_in_background(name="QtSave")
    def _qt_save(self, qimage: QImage, path: Path) -> None:
        if self.is_cancelled:
            return
        qimage.save(str(path), "PNG", 75)  # type: ignore[call-overload]
        self.progress_bar.update_progress(increment=1)


class _SourceInfo(NamedTuple):
    clip: VideoNode
    time_to_frame: Callable[[Time], int]


class SelectFrameWorker:
    # Asymmetric Gaussian curve parameters: (mu, sigma_left, sigma_right)
    DARK_CURVE_PARAMS = (0.065, 0.015, 0.15)
    LIGHT_CURVE_PARAMS = (0.75, 0.45, 0.90)

    def __init__(self, api: PluginAPI, parent: CompPlugin) -> None:
        self.api = api
        self.progress_bar = parent.progress_bar

        self.start = Time.from_qtime(parent.time_edit_start.time())
        self.end = Time.from_qtime(parent.time_edit_end.time())
        self.dark = parent.dark_frame_count.value()
        self.light = parent.light_frame_count.value()
        self.normal = parent.random_frame_count.value() - self.dark - self.light
        self.voutputs = parent.selected_voutputs
        self.curve_points = parent.curve_points
        self.allowed_frame_searches = parent.settings.global_.allowed_frame_searches
        self.brightness_candidates = parent.settings.global_.brightness_candidates
        self.is_cancelled = False

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
        with self.api.blocker(), self.api.vs_context():
            return self.get()

    def cancel(self) -> None:
        self.is_cancelled = True

    def get(self) -> list[tuple[Time, FrameSourceProvider]]:
        if not (frame_range := self._get_frame_range()):
            return []

        start_frame, end_frame = frame_range
        cdf, total_weight = get_probability_cdf(start_frame, end_frame, self.curve_points)
        candidates = range(start_frame, end_frame + 1)

        found_times = list[tuple[Time, FrameSourceProvider]]()

        if self.normal > 0:
            found_times.extend(
                (t, FrameSourceProvider.RANDOM)
                for t in self._sample_frames(
                    count=self.normal,
                    candidates=candidates,
                    cdf=cdf,
                    total_weight=total_weight,
                    progress_fmt="Selecting normal frames %v / %m",
                )
            )

        if self.dark > 0 or self.light > 0:
            # Sample candidate frames for brightness analysis using the temporal probability CDF
            dur = end_frame - start_frame + 1
            if c := self.brightness_candidates:
                num_candidates = min(c, dur)
            else:
                duration = self.api.current_voutput.frame_to_time(dur).total_seconds()
                avg_fps = dur / duration
                num_candidates = min(int(avg_fps * (duration**0.5) * 0.5), dur)

            candidate_frames = set[int]()
            self._itss_sample(
                count=num_candidates,
                candidates=candidates,
                cdf=cdf,
                total_weight=total_weight,
                is_valid_fn=lambda rnum: rnum not in candidate_frames,
                max_attempts=min(self.allowed_frame_searches, num_candidates),
                on_sample_added=candidate_frames.add,
            )

            frames_to_check = sorted(candidate_frames)
            clip_to_check = (
                remap_frames(self.api.current_voutput.vs_output.clip, frames_to_check)
                .resize.Point(format=vs.GRAYS)
                .std.PlaneStats()
            )
            self.progress_bar.update_progress(
                range=(0, len(frames_to_check)), fmt="Checking frames light levels %v / %m", value=0
            )

            def progress_cb(*_: Any) -> None:
                if self.is_cancelled:
                    raise CancelledError("Select frames cancelled")
                self.progress_bar.update_progress(increment=1)

            avg_levels = clip_data_gather(
                clip_to_check,
                progress_cb,
                lambda n, f: get_prop(f, "PlaneStatsAverage", float, func=self.get),
                async_requests=clamp(core.num_threads // 2, 2, clip_to_check.num_frames),
            )

            if self.dark > 0:
                found_times.extend(
                    (t, FrameSourceProvider.RANDOM_DARK)
                    for t in self._get_weighted_brightness_frames(
                        self.dark,
                        frames_to_check,
                        avg_levels,
                        self.DARK_CURVE_PARAMS,
                        "Selecting dark frames %v / %m",
                    )
                )

            if self.light > 0:
                found_times.extend(
                    (t, FrameSourceProvider.RANDOM_LIGHT)
                    for t in self._get_weighted_brightness_frames(
                        self.light,
                        frames_to_check,
                        avg_levels,
                        self.LIGHT_CURVE_PARAMS,
                        "Selecting light frames %v / %m",
                    )
                )

        return sorted(set(found_times))

    def _get_frame_range(self) -> tuple[int, int] | None:
        v = self.api.current_voutput
        start_frame, end_frame = v.time_to_frame(self.start), v.time_to_frame(self.end)

        min_clip_length = min(output.vs_output.clip.num_frames for output in self.voutputs)
        end_frame = min(end_frame, min_clip_length - 1)

        if start_frame > end_frame:
            logger.warning(
                "No valid frame range after clamping to all clips (start=%s, end=%s)",
                start_frame,
                end_frame,
            )
            return None

        return start_frame, end_frame

    def _sample_frames(
        self,
        count: int,
        candidates: Sequence[int],
        cdf: Sequence[float],
        total_weight: float,
        progress_fmt: str,
    ) -> list[Time]:
        self.progress_bar.update_progress(range=(0, count), fmt=progress_fmt, value=0)

        v = self.api.current_voutput
        base_clip = core.std.BlankClip(width=1, height=1, format=GRAY8, length=len(self.voutputs), keep=True)
        other_clips = [_SourceInfo(output.vs_output.clip, output.time_to_frame) for output in self.voutputs]

        def is_valid_fn(rnum: int) -> bool:
            if rnum in self.checked:
                return False

            if not (self.should_check_pict or self.should_check_combed):
                return True

            # Convert frame -> time, then use each clip's own time_to_frame to get a valid frame index
            # for that specific clip
            rtime = v.frame_to_time(rnum)
            node_frames = core.std.FrameEval(
                base_clip,
                lambda n: base_clip.std.CopyFrameProps(
                    (c := other_clips[n]).clip[c.time_to_frame(rtime)], props=["_PictType", "_Combed"]
                ),
            )

            for f in node_frames.frames(close=True):
                if self.is_cancelled:
                    raise CancelledError("Select frames cancelled")

                is_pict_type_not_selected = (
                    self.should_check_pict
                    and get_prop(f, "_PictType", str, default="", func="__vsview__") not in self.pict_types
                )
                is_combed = (
                    self.should_check_combed  # No format
                    and get_prop(f, "_Combed", int, default=0, func="__vsview__")
                )

                if is_pict_type_not_selected or is_combed:
                    logger.log(
                        DEBUG - 1,
                        "Invalid frame found: %s. PictType not selected: %s. Combed: %s",
                        rnum,
                        is_pict_type_not_selected,
                        is_combed,
                    )
                    return False
            return True

        selected_times = list[Time]()

        def on_sample_added(rnum: int) -> None:
            self.checked.append(rnum)
            selected_times.append(v.frame_to_time(rnum))
            logger.log(DEBUG - 1, "Valid frame found: %s", rnum)
            self.progress_bar.update_progress(value=len(selected_times))

        self._itss_sample(
            count=count,
            candidates=candidates,
            cdf=cdf,
            total_weight=total_weight,
            is_valid_fn=is_valid_fn,
            max_attempts=self.allowed_frame_searches,
            on_sample_added=on_sample_added,
        )

        return selected_times

    def _itss_sample(
        self,
        count: int,
        candidates: Sequence[int],
        cdf: Sequence[float],
        total_weight: float,
        is_valid_fn: Callable[[int], bool],
        max_attempts: int,
        on_sample_added: Callable[[int], None],
    ) -> None:
        # Perform Inverse Transform Stratified Sampling (ITSS)
        for j in range(count):
            if self.is_cancelled:
                raise CancelledError("Select frames cancelled")

            lo_val = j * (total_weight / count)
            hi_val = (j + 1) * (total_weight / count)
            logger.log(DEBUG - 1, "lo_val: %.4f, hi_val: %.4f, total_weight: %.4f", lo_val, hi_val, total_weight)

            rnum = -1
            for k in range(max_attempts):
                logger.log(DEBUG - 1, "k: %s", k)

                u = random.uniform(lo_val, hi_val)
                idx = clamp(bisect_left(cdf, u), 0, len(cdf) - 1)
                rnum = candidates[idx]
                logger.log(DEBUG - 1, "u: %.4f, idx: %s, rnum: %s", u, idx, rnum)

                if is_valid_fn(rnum):
                    on_sample_added(rnum)
                    break
            else:
                logger.warning("Maximum number of attempts reached while searching for attempt %s", j)

    def _get_weighted_brightness_frames(
        self,
        count: int,
        frames_to_check: list[int],
        avg_levels: list[float],
        curve_params: tuple[float, float, float],
        progress_fmt: str,
    ) -> list[Time]:
        weights = [asymmetric_gaussian(level, *curve_params) for level in avg_levels]
        cdf, total_weight = build_cdf(weights)

        return self._sample_frames(
            count=count,
            candidates=frames_to_check,
            cdf=cdf,
            total_weight=total_weight,
            progress_fmt=progress_fmt,
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
    async def search(self, query: str) -> list[TMDBTitle]:
        titles = list[TMDBTitle]()

        async with (
            niquests.AsyncSession(
                base_url=self.BASE_URL,
                timeout=10,
                headers={"Authorization": f"Bearer {self.api_key}"},
                params=self.BASE_PARAMS,
                disable_http3=True,
                multiplexed=True,
                revocation_configuration=REV_CONF,
            ) as client,
            LogNiquestsErrors("TMDB search"),
        ):
            tv_resp = await client.get("/search/tv", params={"query": query})
            await client.gather(tv_resp)

            num_tv_task = None
            num_movie_task = None

            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._ensure_genres_loaded(client))
                movie_task = tg.create_task(client.get("/search/movie", params={"query": query}))

                if query.isnumeric():
                    num_tv_task = tg.create_task(client.get(f"/tv/{query}"))
                    num_movie_task = tg.create_task(client.get(f"/movie/{query}"))

            movie_resp = movie_task.result()
            await client.gather(movie_resp)

            title_tasks = list[TMDBTitle]()

            tv_data = TMDBPayload.validate_logged(tv_resp.raise_for_status().json(), "TMDB /search/tv")
            if tv_data:
                title_tasks.extend(self._create_title(item, "tv") for item in tv_data.results)

            movie_data = TMDBPayload.validate_logged(movie_resp.raise_for_status().json(), "TMDB /search/movie")
            if movie_data:
                title_tasks.extend(self._create_title(item, "movie") for item in movie_data.results)

            titles.extend(title_tasks)

            if num_tv_task and num_movie_task:
                for res, genre_type in zip([num_tv_task.result(), num_movie_task.result()], ["tv", "movie"]):
                    await client.gather(res)
                    item = TMDBTitleData.validate_logged(res.json(), f"TMDB /{genre_type}/{query}")
                    if item:
                        titles.append(self._create_title(item, genre_type))  # type: ignore[arg-type]
        return titles

    async def _ensure_genres_loaded(self, client: niquests.AsyncSession) -> None:
        if not self._tv_genres or not self._movie_genres:
            tv_req = client.get("/genre/tv/list")
            movie_req = client.get("/genre/movie/list")

            tv_res, movie_res = await asyncio.gather(tv_req, movie_req, return_exceptions=True)

            if isinstance(tv_res, niquests.Response) and not self._tv_genres:
                await client.gather(tv_res)
                data = TMDBPayload.validate_logged(tv_res.raise_for_status().json(), "TMDB /genre/tv/list")
                if data:
                    self._tv_genres = {g.id: g.name for g in data.genres}

            if isinstance(movie_res, niquests.Response) and not self._movie_genres:
                await client.gather(movie_res)
                data = TMDBPayload.validate_logged(movie_res.raise_for_status().json(), "TMDB /genre/movie/list")
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

    def __init__(
        self,
        api: PluginAPI,
        settings: PluginSettings[GlobalSettings, None],
        secrets: PluginSecrets,
        progress_bar: ProgressBar,
    ) -> None:
        self.api = api
        self.settings = settings
        self.secrets = secrets
        self.progress_bar = progress_bar
        self.headers = get_slowpics_headers()
        self.is_cancelled = False
        self._upload_task: asyncio.Task[Any] | None = None
        self._upload_loop: asyncio.AbstractEventLoop | None = None

    def cancel(self) -> None:
        self.is_cancelled = True
        if self._upload_task and self._upload_loop:
            with suppress(Exception):
                self._upload_loop.call_soon_threadsafe(self._upload_task.cancel)

    @run_in_background(name="SlowPicsTags")
    def get_tags(self) -> list[Tag]:
        with (
            niquests.Session(base_url=self.BASE_URL, headers=self.headers) as client,
            LogNiquestsErrors("Slowpics Tags"),
        ):
            tags = client.get("/api/tags").raise_for_status().json()
            return [Tag(tag["value"], tag["label"].strip()) for tag in tags]

    @run_in_background(name="SlowPicsLogin")
    async def get_cookies(self) -> dict[str, str]:
        if cookies := self.secrets.get_json(COOKIE_KEY, COOKIE_KEY):
            return cookies

        if not self.secrets.get_credential(LOGIN_CONTEXT):
            return {}

        async with (
            niquests.AsyncSession(
                base_url=self.BASE_URL,
                headers=self.headers,
                timeout=20,
                disable_http3=True,
                multiplexed=True,
                revocation_configuration=REV_CONF,
            ) as client,
            LogNiquestsErrors("Slowpics Login"),
        ):
            # Grab initial XSRF token
            resp = await client.get("/comparison")
            await client.gather(resp)
            resp.raise_for_status()

            return self._cookies_jar(client.cookies) if await self._login_async(client) else {}

    @run_in_background(name="SlowPicsUpload")
    async def upload(self, *, src: SlowPicsSources, cookies: dict[str, str]) -> str:
        self.is_cancelled = False
        self._upload_task = asyncio.current_task()
        self._upload_loop = asyncio.get_running_loop()
        self._sema = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

        try:
            async with (
                niquests.AsyncSession(
                    base_url=self.BASE_URL,
                    pool_maxsize=self.MAX_CONCURRENT_REQUESTS,
                    pool_connections=self.MAX_CONCURRENT_REQUESTS,
                    headers=self.headers,
                    cookies=cookies,
                    timeout=20,
                    disable_http3=True,
                    multiplexed=True,
                    revocation_configuration=REV_CONF,
                ) as client,
                LogNiquestsErrors("Slowpics Upload"),
            ):
                logger.debug("Setup client")
                await self._setup_client(client, cookies)

                logger.debug("Calculating image hashes")
                image_hashes = await self._hash_images(src)

                logger.debug("Starting upload of: %s", src.collection_name)
                start_resp = await client.post(
                    url=f"/upload/{src.upload_type}",
                    data=src.payload | {"browserId": self.settings.global_.browser_id} | image_hashes,
                )
                await client.gather(start_resp)
                comp_data = SlowPicsUploadResponse.model_validate(start_resp.raise_for_status().json())

                collection_url = f"https://slow.pics/c/{comp_data.key}"
                logger.debug("Starting upload of: %s", collection_url)

                # Upload images concurrently
                self.progress_bar.update_progress(range=(0, src.total_images), fmt="Uploading images %v / %m", value=0)

                async def upload_with_progress(img_uuid: str, img_path: Path) -> None:
                    if self.is_cancelled:
                        raise asyncio.CancelledError("Slowpics upload cancelled")
                    logger.debug("Uploading image: %s", img_uuid)
                    await self._upload_image(client, comp_data.collection_uuid, img_uuid, img_path)
                    logger.debug("Finished uploading image: %s", img_uuid)
                    self.progress_bar.update_progress(increment=1)

                async with asyncio.TaskGroup() as tg:
                    for img_uuid, image_path in src.get_images(comp_data):
                        if self.is_cancelled:
                            raise asyncio.CancelledError("Slowpics upload cancelled")
                        tg.create_task(upload_with_progress(img_uuid, image_path))

                logger.debug("Finished uploading all images")
                self.progress_bar.update_progress(fmt="Finished uploading %v images")

                # Refresh cookies
                self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, self._cookies_jar(client.cookies))
                logger.debug("Refreshed cookies")

                return collection_url
        finally:
            self._upload_task = None
            self._upload_loop = None

    async def _setup_client(self, client: niquests.AsyncSession, cookies: dict[str, str]) -> None:
        homepage = await client.get("/comparison")
        await client.gather(homepage)
        homepage.raise_for_status()

        if not (browser_id := self._get_cookie(client.cookies, "BROWSER-ID")):
            niquests.cookies.cookiejar_from_dict(
                {"BROWSER-ID": self.settings.global_.browser_id},
                cookiejar=client.cookies,
            )
        else:
            self.settings.global_.browser_id = browser_id

        client.headers.update({"X-XSRF-TOKEN": self._get_cookie(client.cookies, "XSRF-TOKEN")})

        if cookies:
            if homepage.text is not None and 'id="logoutBtn"' in homepage.text:
                logger.debug("Cookies not stale, logged in.")
                self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, self._cookies_jar(client.cookies))
            else:
                logger.info("Cookies have expired (or Logout button not found), attempting re-login...")
                self.secrets.delete_json(COOKIE_KEY, COOKIE_KEY)

                if await self._login_async(client):
                    logger.info("Re-login successful.")
                    client.headers.update({"X-XSRF-TOKEN": self._get_cookie(client.cookies, "XSRF-TOKEN")})
                else:
                    logger.warning("Re-login failed, uploading as anonymous")

    async def _login_async(self, client: niquests.AsyncSession) -> bool:
        if not (credentials := self.secrets.get_credential(LOGIN_CONTEXT)):
            return False

        try:
            client.headers.update({"X-XSRF-TOKEN": self._get_cookie(client.cookies, "XSRF-TOKEN")})

            login_page = await client.get("/login")
            await client.gather(login_page)
            login_page.raise_for_status()

            csrf_match = re.search(
                r'<input type="hidden" name="_csrf" value="([a-zA-Z0-9-_]+)"\/>',
                login_page.text or "",
            )
            if not csrf_match:
                logger.error("No CSRF found; Failed to login.")
                return False

            data = {
                "username": credentials.username,
                "password": credentials.password,
                "_csrf": csrf_match.group(1),
                "remember-me": "on",
            }
            login_resp = await client.post("/login", data=data, allow_redirects=True)
            await client.gather(login_resp)
            login_resp.raise_for_status()

            self.secrets.set_json(COOKIE_KEY, COOKIE_KEY, self._cookies_jar(client.cookies))
            return True
        except Exception as e:
            logger.error("Async login sequence failed: %s", e)
            logger.debug("Full traceback", exc_info=True)
            return False

    async def _upload_image(
        self,
        client: niquests.AsyncSession,
        collection: str,
        image_uuid: str,
        image_path: Path,
    ) -> None:
        url = f"/upload/image/{image_uuid}"
        data = {"collectionUuid": collection, "imageUuid": image_uuid, "browserId": self.settings.global_.browser_id}

        # Handle 429 "Too many requests"
        for retry in range(5):
            if self.is_cancelled:
                raise asyncio.CancelledError("Slowpics upload cancelled")
            try:
                async with self._sema:
                    response = await client.post(
                        url,
                        data=data,
                        files={"file": (image_path.name, await asyncio.to_thread(image_path.read_bytes), "image/png")},
                    )
                    await client.gather(response)
                    if response.status_code == 400:
                        error_message = response.headers.get("X-Error-Message", "")
                        # Image exists on the server already
                        if error_message == "IMAGE_IS_COMPLETE":
                            logger.warning("Image %s already existed on the server.", image_path.name)
                            return
                        # Slowbro said any other errors should stop the upload
                        logger.warning("Image %s has an unhandled error %s", image_path.name, error_message)
                        raise UploadError("Upload has an unhandled error, stopping upload")
                    if response.status_code == 429:
                        wait_time = int(response.headers.get("Retry-After", (retry + 1) * 2))
                        logger.warning("Rate limited for %s. Waiting %ds...", image_path.name, wait_time)
                        await asyncio.sleep(wait_time)
                        continue

                response.raise_for_status()
                return
            except UploadError:
                self.cancel()
                raise
            except Exception as e:
                if retry == 4:
                    raise
                logger.error("Error uploading %s (attempt %d) '%s'", image_path.name, retry + 1, e)
                logger.debug("Traceback:", exc_info=True)
                await asyncio.sleep((retry + 1) * 2)

    async def _hash_images(self, src: SlowPicsSources) -> dict[str, str]:
        payload = dict[str, str]()

        def _hash_image(file: Path, image_key: str) -> None:
            with file.open("rb") as f:
                payload[image_key] = hashlib.file_digest(f, "sha256").hexdigest()

        async with asyncio.TaskGroup() as tg:
            for j in range(len(src.sources[0].images)):
                for i, (_, images) in enumerate(src.sources):
                    tg.create_task(
                        asyncio.to_thread(
                            _hash_image,
                            images[j].path,
                            f"comparisons[{j}].images[{i}].hashSum" if src.is_comparison else f"images[{j}].hashSum",
                        )
                    )

        return payload

    @staticmethod
    def _cookies_jar(cookies: CookieJar) -> dict[str, str]:
        return {c.name: c.value or "" for c in cookies}

    @staticmethod
    def _get_cookie(jar: CookieJar, name: str) -> str:
        for cookie in jar:
            if cookie.name == name and cookie.value:
                return cookie.value
        return ""
