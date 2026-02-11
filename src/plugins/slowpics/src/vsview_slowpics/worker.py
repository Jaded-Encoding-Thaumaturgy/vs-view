import asyncio
import logging
import math
import random
import re
import string
from collections import defaultdict
from enum import IntEnum, auto
from itertools import chain
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4

import httpx
import vapoursynth as vs
from jetpytools import ndigits
from PySide6.QtCore import QObject, Signal, Slot
from vstools import clip_data_gather, get_prop, remap_frames

from vsview.app.plugins.api import PluginAPI
from vsview.app.settings.models import GLOBAL_SETTINGS_PATH

from .settings import GlobalSettings
from .utils import get_slowpics_headers

logger = logging.getLogger("vsview-slowpics")


class SPFrameSource(IntEnum):
    RANDOM = auto()
    RANDOM_DARK = auto()
    RANDOM_LIGHT = auto()
    MANUAL = auto()
    CURRENT = auto()


class SPFrame:
    frame: int
    frame_type: SPFrameSource

    def __init__(self, value: int, source: SPFrameSource):
        self.frame = value
        self.frame_type = source

    def __repr__(self) -> str:
        return f"SPFrame(frame={self.frame}, frame_type={self.frame_type})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SPFrame):
            return False
        return self.frame == other.frame

    def __hash__(self) -> int:
        return hash(self.frame)


# For extraction


class SlowPicsFramesData(NamedTuple):
    random_frames: int
    random_min: int
    random_max: int | None
    random_dark: int
    random_light: int
    pict_types: set[str]
    current_frame: bool


class SlowPicsUploadInfo(NamedTuple):
    name: str
    public: bool
    nsfw: bool
    tmdb: str | None
    remove_after: int
    tags: list[str]


class SlowPicsImageData(NamedTuple):
    path: Path
    frames: list[SPFrame]


# For upload


class SlowPicsUploadImage(NamedTuple):
    path: Path
    image_type: str
    frame_no: int
    timestamp: str


class SlowPicsUploadSource(NamedTuple):
    name: str
    images: list[SlowPicsUploadImage]


class SlowPicsUploadData(NamedTuple):
    info: SlowPicsUploadInfo
    sources: list[SlowPicsUploadSource]


class SlowPicsWorker(QObject):
    ALLOWED_FRAME_SEARCHES = 150

    progress = Signal(int)
    progressFormat = Signal(str)
    progressRange = Signal(int, int)
    jobFinished = Signal(str, object, bool)
    updateSettings = Signal(object)

    def __init__(self, api: PluginAPI, settings: GlobalSettings, parent: QObject | None = None):
        super().__init__(parent)

        self.api = api

        self.semaphore = asyncio.Semaphore(20)
        self.browser_id = str(uuid4())

        self.has_login = len(settings.cookies) > 0
        self.settings = settings

    @Slot(str, object)
    def do_work(self, job_name: str, params: Any, do_next: bool) -> None:
        with self.api.vs_context():
            if job_name == "frames":
                frames = self.get_frames(params)
                self.jobFinished.emit(job_name, frames, do_next)
            elif job_name == "extract":
                extract = self.get_upload_data(params)
                self.jobFinished.emit(job_name, extract, do_next)
            elif job_name == "upload":
                slow = asyncio.run(self.upload_slowpics(params))
                self.jobFinished.emit(job_name, slow, do_next)
            elif job_name == "login":
                login = asyncio.run(self.login(params))
                self.jobFinished.emit(job_name, login, do_next)
            else:
                logger.warning("Running unknown job %s", job_name)
                self.progressFormat.emit(f"Unknown job: {job_name}")
                self.jobFinished.emit(job_name, None, do_next)

    def update_settings(self, settings: GlobalSettings) -> None:
        logger.debug("Worker updated settings")
        self.settings = settings

    def get_frames(self, frame_info: SlowPicsFramesData) -> list[SPFrame]:

        self.checked = list[int]()

        random_max = frame_info.random_max or min(source.vs_output.clip.num_frames - 1 for source in self.api.voutputs)

        if (random_max - frame_info.random_min) < frame_info.random_frames:
            raise ValueError("Cannot generate enough frames with this range of frames.")

        found_frames = []

        if frame_info.current_frame:
            found_frames.append(SPFrame(self.api.current_frame, SPFrameSource.CURRENT))

        if len(frame_info.pict_types) != 3:
            found_frames.extend(
                self._get_random_frames(
                    frame_info.random_frames, frame_info.pict_types, frame_info.random_min, random_max
                )
            )

        if frame_info.random_light or frame_info.random_dark:
            found_frames.extend(
                self._get_random_by_light_level(
                    frame_info.random_light, frame_info.random_dark, frame_info.random_min, random_max
                )
            )

        self.progressRange.emit(0, len(found_frames))
        self.progress.emit(len(found_frames))
        self.progressFormat.emit("Extracted images %v / %m")

        return sorted(set(found_frames), key=lambda x: x.frame)

    def _get_random_number(self, min: int, max: int) -> int:

        while (rnum := random.randint(min, max)) in self.checked:
            pass

        self.checked.append(rnum)
        return rnum

    def _get_random_number_interval(self, min: int, max: int, random_count: int, index: int) -> int:
        if random_count < index or index < 0:
            raise ValueError(f"{index} is out of range of 0-{random_count - 1}")

        interval = math.floor((max - min) / random_count)
        return min + self._get_random_number(interval * index, interval * (index + 1))

    def _get_random_frames(
        self, random_count: int, pict_types: set[str], random_min: int, random_max: int
    ) -> list[SPFrame]:

        self.progressFormat.emit("Random Frames by Pict %v / %m")
        self.progressRange.emit(0, random_count)

        pict_types_b = [pict_type.encode() for pict_type in pict_types]

        should_check_pict = len(pict_types) != 3

        random_frames: list[SPFrame] = []

        while len(random_frames) < random_count:
            attempts = 0
            while True:
                if attempts > self.ALLOWED_FRAME_SEARCHES:
                    logger.warning(
                        "%s attempts were made and only found %s frames "
                        "and no match found for %s; stopping iteration...",
                        self.ALLOWED_FRAME_SEARCHES,
                        len(random_frames),
                        pict_types,
                    )
                    break

                rnum = self._get_random_number_interval(random_min, random_max, random_count, len(random_frames))
                if self.api.current_timeline_mode == "time":
                    timestamp = self.api.voutputs[0].frame_to_time(rnum)
                    frames = [source.vs_output.clip[source.time_to_frame(timestamp)] for source in self.api.voutputs]
                else:
                    frames = [source.vs_output.clip[rnum] for source in self.api.voutputs]

                for f in vs.core.std.Splice(frames, True).frames(close=True):
                    pict_type = get_prop(f.props, "_PictType", str, default="", func="__vsview__")
                    if should_check_pict and pict_type.encode() not in pict_types_b:
                        logger.debug("Ignoring frame %s due to '%s' PictType not in %s", rnum, pict_type, pict_types)
                        break

                    # Bad for vivtc/interlaced sources
                    if get_prop(f.props, "_Combed", int, default=0, func="__vsview__"):
                        logger.debug("Ignoring frame %s due to being combed", rnum)
                        break
                else:
                    # This will only be hit if the above for loop didn't break
                    random_frames.append(SPFrame(rnum, SPFrameSource.RANDOM))
                    self.progress.emit(len(random_frames))
                    break

                attempts += 1

        return random_frames

    def _get_random_by_light_level(self, light: int, dark: int, random_min: int, random_max: int) -> list[SPFrame]:

        frame_level: dict[float, list[int]] = defaultdict(list)

        clip = self.api.voutputs[0].vs_output.clip
        frames = list(range(random_min, random_max, int((random_max - random_min) / (self.ALLOWED_FRAME_SEARCHES * 3))))

        checked = 0
        self.progressFormat.emit("Checking frames light levels %v / %m")
        self.progressRange.emit(0, len(frames))

        def _progress(a: int, b: int) -> None:
            nonlocal checked
            checked += 1
            self.progress.emit(checked)

        decimated = remap_frames(clip.std.PlaneStats(), frames)
        image_types = clip_data_gather(
            decimated,
            _progress,
            lambda a, f: get_prop(f.props, "PlaneStatsAverage", float, default=0, func="__vspreview__"),
        )

        for i, f in enumerate(image_types):
            frame_level[f].append(frames[i])

        dark_to_light = list(chain.from_iterable(frame_level[k] for k in sorted(frame_level)))
        darkest = [SPFrame(frame, SPFrameSource.RANDOM_DARK) for frame in dark_to_light[:dark]]
        lightest = [SPFrame(frame, SPFrameSource.RANDOM_LIGHT) for frame in dark_to_light[-light:]]

        return lightest + darkest

    def get_upload_data(self, data: SlowPicsImageData) -> list[SlowPicsUploadSource]:
        base_path = data.path / "".join(random.choices(string.ascii_uppercase + string.digits, k=16))

        frames_n = [f.frame for f in data.frames]

        self.progressRange.emit(0, len(frames_n))

        def _frame_callback(n: int, f: vs.VideoFrame) -> str:
            return get_prop(f.props, "_PictType", str, default="?", func="__vsview__")

        sources = list[SlowPicsUploadSource]()

        for source in self.api.voutputs:
            name = source.vs_name or f"Node {source.vs_index}"

            images = 0
            self.progressFormat.emit(f"Extracting {name} %v / %m")

            def _progress(a: int, b: int) -> None:
                nonlocal images
                images += 1
                self.progress.emit(images)

            safe_folder = "".join(x for x in name if x.isalnum() or x.isspace())
            if not safe_folder:
                safe_folder = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))

            image_path = (base_path / safe_folder) / f"%0{ndigits(max(frames_n))}d.png"

            image_path.parent.mkdir(parents=True, exist_ok=True)

            clip = self.api.packer.to_rgb_planar(source.vs_output.clip, format=vs.RGB24)
            clip = vs.core.fpng.Write(clip, filename=str(image_path), compression=1)

            decimated = remap_frames(clip, frames_n)
            image_types = clip_data_gather(decimated, _progress, _frame_callback)

            logger.debug("Saving images to: %s", image_path.parent)

            sources.append(
                SlowPicsUploadSource(
                    name,
                    [
                        SlowPicsUploadImage(
                            Path(str(image_path) % frame),
                            image_types[framec],
                            frame,
                            source.frame_to_time(frame).to_ts("{M:02d}:{S:02d}.{ms:03d}"),
                        )
                        for framec, frame in enumerate(frames_n)
                    ],
                )
            )

        return sources

    async def setup_http_client(self, client: httpx.AsyncClient, is_login: bool = False) -> None:
        self.has_login = len(self.settings.cookies) > 0
        if self.has_login and not is_login:
            logger.debug("Logging in")
            for key, value in self.settings.cookies.items():
                client.cookies.set(key, value)

        homepage = (await client.get("https://slow.pics/comparison")).raise_for_status()

        client.headers.update({"X-XSRF-TOKEN": client.cookies.get("XSRF-TOKEN") or ""})

        if self.has_login and not is_login:
            if 'id="logoutBtn"' not in homepage.text:
                logger.error("Cookies have expired")
            else:
                logger.debug("Cookies not stale, logged in.")
                self.save_cookies(client)

    def save_cookies(self, client: httpx.AsyncClient, is_login: bool = False) -> None:
        if not is_login and not self.has_login:
            return

        self.updateSettings.emit(dict(client.cookies.items()))

    async def upload_slowpics(self, data: SlowPicsUploadData) -> str:
        """Takes SlowPicsUploadData and uploads to slow.pics based on parameters"""

        async with httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
            headers=get_slowpics_headers(),
            timeout=20,
        ) as client:
            await self.setup_http_client(client)

            is_comparison = len(data.sources) > 1

            total_images = sum(len(source.images) for source in data.sources)

            comp_upload = {}

            for i, source in enumerate(data.sources):
                for j, image in enumerate(source.images):
                    # TODO: This time_str may need to per image as it may be different on VFR sources
                    time_str = f"{image.timestamp} / {image.frame_no}"

                    if is_comparison:
                        comp_upload[f"comparisons[{j}].name"] = time_str
                        comp_upload[f"comparisons[{j}].imageNames[{i}]"] = f"({image.image_type}) {source.name}"
                    else:
                        comp_upload[f"imageNames[{j}]"] = f"{time_str} - {source.name}"

            tags = {}
            if data.info.public:
                tags = {f"tags[{i}]": tag for i, tag in enumerate(data.info.tags)}

            comp_info = {
                "collectionName": data.info.name,
                "hentai": str(data.info.nsfw).lower(),
                "optimizeImages": "true",
                "browserId": self.browser_id,
                "public": str(data.info.public).lower(),
            }

            if data.info.tmdb:
                comp_info["tmdbId"] = data.info.tmdb
            if data.info.remove_after >= 1:
                comp_info["removeAfter"] = str(data.info.remove_after)

            comp_data = (
                (
                    await client.post(
                        f"https://slow.pics/upload/{'comparison' if is_comparison else 'collection'}",
                        data=comp_upload | tags | comp_info,
                    )
                )
                .raise_for_status()
                .json()
            )

            collection = comp_data["collectionUuid"]
            key = comp_data["key"]
            image_ids = comp_data["images"]

            logger.debug("String upload of: https://slow.pics/c/%s", key)

            reqs = []
            self.progressRange.emit(0, total_images)
            self.progressFormat.emit("Uploading images %v / %m")
            self.progress.emit(0)
            for i, source in enumerate(data.sources):
                for j, image in enumerate(source.images):
                    image_uuid = image_ids[j][i] if is_comparison else image_ids[0][j]
                    reqs.append(self.upload_image(client, collection, image_uuid, image))

            for i, resp in enumerate(asyncio.as_completed(reqs), start=1):
                await resp
                self.progress.emit(i)

            self.progressFormat.emit("Finished uploading %v images")

            self.save_cookies(client)

            return f"https://slow.pics/c/{key}"

    async def upload_image(
        self, client: httpx.AsyncClient, collection: str, image_uuid: str, image: SlowPicsUploadImage
    ) -> httpx.Response:
        async with self.semaphore:
            return (
                await client.post(
                    f"https://slow.pics/upload/image/{image_uuid}",
                    data={
                        "collectionUuid": collection,
                        "imageUuid": image_uuid,
                        "browserId": self.browser_id,
                    },
                    files={
                        "file": (image.path.name, image.path.read_bytes(), "image/png"),
                    },
                )
            ).raise_for_status()

    async def login(self, login_data: dict[str, str]) -> bool:
        async with httpx.AsyncClient(headers=get_slowpics_headers(), timeout=20) as client:
            await self.setup_http_client(client, True)

            login_page = (await client.get("https://slow.pics/login")).raise_for_status()

            csrf = re.search(r'<input type="hidden" name="_csrf" value="([a-zA-Z0-9-_]+)"\/>', login_page.text)

            if not csrf:
                logger.error("Failed to login!")
                return False

            login_data["_csrf"] = csrf.group(1)
            login_data["remember-me"] = "on"

            print(login_data)

            (await client.post("https://slow.pics/login", data=login_data, follow_redirects=True)).raise_for_status()

            self.save_cookies(client, True)

            logger.debug("Logged in saving cookies.")
            logger.debug("%s", GLOBAL_SETTINGS_PATH)

        return True
