import asyncio
import logging
import math
import random
import string
from collections import defaultdict
from collections.abc import Iterable
from enum import IntEnum, auto
from itertools import chain, islice
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

import httpx
import vapoursynth as vs
from jetpytools import ndigits
from PySide6.QtCore import QObject, Signal, Slot
from vstools import clip_data_gather, get_prop, remap_frames

from vsview.app.plugins.api import PluginAPI, VideoOutputProxy
from vsview.app.views.timeline import Time

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

    def __repr__(self):
        return f"SPFrame(frame={self.frame}, frame_type={self.frame_type})"
    def __eq__(self, other):
        if not isinstance(other, SPFrame):
            return NotImplemented
        return self.frame == other.frame

    def __hash__(self):
        return hash(self.frame)

# For extraction

class SlowPicsFramesData(NamedTuple):
    random_frames: int
    random_min: int
    random_max: int | None
    random_dark: int | None
    random_light: int | None
    pict_types: set[str]
    current_frame: bool

class SlowPicsUploadInfo(NamedTuple):
    name: str
    public: bool
    nsfw: bool
    tmdb: str | None
    remove_after: int | None
    tags: Iterable[str]

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
    images: Iterable[SlowPicsUploadImage]

class SlowPicsUploadData(NamedTuple):
    info: SlowPicsUploadInfo
    sources: Iterable[SlowPicsUploadSource]

class SlowPicsWorker(QObject):
    ALLOWED_FRAME_SEARCHES = 150

    progress = Signal(int)
    format = Signal(str)
    range = Signal(int, int)
    finished = Signal(str, object, bool)


    api: PluginAPI = None

    # Didn't like __init__ ?
    def setApi(self, api: PluginAPI):
        self.api = api

    @Slot(str, object)
    def do_work(self, job_name:str, params:object, do_next:bool):
        with self.api.vs_context():
            if job_name == "frames":
                frames = self.get_frames(params)
                self.finished.emit(job_name, frames, do_next)
            elif job_name == "extract":
                extract = self.get_upload_data(params)
                self.finished.emit(job_name, extract, do_next)
            elif job_name == "upload":
                slow = asyncio.run(self.upload_slowpics(params))
                self.finished.emit(job_name, slow, do_next)
            else:
                logger.warning("Running unknown job %s", job_name)
                self.format.emit(f"Unknown job: {job_name}")
                self.finished.emit(job_name, None, do_next)


    def get_frames(self, frame_info: SlowPicsFramesData) -> list[SPFrame]:

        self.checked = []

        random_max = frame_info.random_max or min([source.vs_output.clip.num_frames-1 for source in self.api.voutputs])

        if (random_max - frame_info.random_min) < frame_info.random_frames:
            raise ValueError("Cannot generate enough frames with this range of frames.")


        found_frames = []

        if frame_info.current_frame:
            found_frames.append(SPFrame(self.api.current_frame, SPFrameSource.CURRENT))


        if len(frame_info.pict_types) != 3:
            found_frames.extend(
                self._get_random_frames(
                    frame_info.random_frames,
                    frame_info.pict_types,
                    frame_info.random_min,
                    random_max
                )
            )

        if frame_info.random_light or frame_info.random_dark:
            found_frames.extend(
                self._get_random_by_light_level(
                    frame_info.random_light,
                    frame_info.random_dark,
                    frame_info.random_min,
                    random_max
                )
            )

        self.range.emit(0, len(found_frames))
        self.progress.emit(len(found_frames))
        self.format.emit("Extracted images %v / %m")

        return sorted(set(found_frames), key=lambda x: x.frame)


    def _get_random_number(self, min:int, max:int) -> int:

        while (rnum := random.randint(min, max)) in self.checked:
            pass

        self.checked.append(rnum)
        return rnum

    def _get_random_number_interval(self, min:int, max:int, random_count: int, index:int) -> int:
        if random_count < index or index < 0:
            raise ValueError(f"{index} is out of range of 0-{random_count-1}")

        interval = math.floor((max-min) / random_count)
        return min + self._get_random_number(interval * index, interval * (index + 1))


    def _get_random_frames(self, random_count: int, pict_types: set[str],
                           random_min: int, random_max: int) -> list[SPFrame]:

        self.format.emit("Random Frames by Pict %v / %m")
        self.range.emit(0, random_count)

        pict_types_b = [pict_type.encode() for pict_type in pict_types]

        should_check_pict = len(pict_types) != 3

        random_frames = []

        while len(random_frames) < random_count:
            attempts = 0
            while True:
                if attempts > self.ALLOWED_FRAME_SEARCHES:
                    logger.warning(
                        "%s attempts were made and only found %s frames "
                        "and no match found for %s; stopping iteration...",
                        self.ALLOWED_FRAME_SEARCHES,
                        len(random_frames),
                        pict_types
                    )
                    break

                rnum = self._get_random_number_interval(random_min, random_max, random_count, len(random_frames))
                frames = [source.vs_output.clip[rnum] for source in self.api.voutputs.values()]


                for f in vs.core.std.Splice(frames, True).frames(close=True):
                    pict_type = get_prop(f.props, "_PictType", str, default="", func="__vsview__")
                    if should_check_pict and pict_type.encode() not in pict_types_b:
                        break

                    # Bad for vivtc/interlaced sources
                    if get_prop(f.props, "_Combed", int, default=0, func="__vsview__"):
                        break
                else:
                    # This will only be hit if the above for loop didn't break
                    random_frames.append(SPFrame(rnum, SPFrameSource.RANDOM))
                    self.progress.emit(len(random_frames))
                    break

                attempts += 1

        return random_frames

    def _get_random_by_light_level(self, light: int, dark:int, random_min: int, random_max: int) -> list[SPFrame]:

        frame_level: dict[float, list[int]] = defaultdict(list)

        clip = self.api.voutputs[0].vs_output.clip
        frames = list(range(random_min, random_max, int((random_max-random_min)/(self.ALLOWED_FRAME_SEARCHES * 3))))

        checked = 0
        self.format.emit("Checking frames light levels %v / %m")
        self.range.emit(0, len(frames))
        def _progress(a, b):
            nonlocal checked
            checked += 1
            self.progress.emit(checked)

        decimated = remap_frames(clip.std.PlaneStats(), frames)
        image_types = clip_data_gather(
            decimated,
            _progress,
            lambda a, f: get_prop(f.props, "PlaneStatsAverage", float, default=0, func="__vspreview__")
        )

        for i, f in enumerate(image_types):
            frame_level[f].append(frames[i])

        print(frame_level)

        return [
            SPFrame(i, SPFrameSource.RANDOM_LIGHT)
            for i in islice(chain.from_iterable(frame_level[k] for k in sorted(frame_level)), light)
        ] + \
        [
            SPFrame(i, SPFrameSource.RANDOM_DARK)
            for i in islice(chain.from_iterable(frame_level[k] for k in sorted(frame_level, reverse=True)), dark)
        ]

    def get_upload_data(self, data: SlowPicsImageData) -> list[SlowPicsUploadSource]:
        base_path = data.path / "".join(random.choices(string.ascii_uppercase + string.digits, k=16))

        frames_n = [f.frame for f in data.frames]


        self.range.emit(0, len(frames_n))

        def _frame_callback(n: int, f: vs.VideoFrame) -> str:
            return get_prop(f.props, "_PictType", str, default="?", func="__vsview__")

        def _handle_image_info( source: VideoOutputProxy, frame: int, image_type:str, path:Path) -> SlowPicsUploadImage:
            clip = source.vs_output.clip
            seconds = frame * clip.fps_den / clip.fps_num if clip.fps_num > 0 else 0
            return SlowPicsUploadImage(
                path,
                image_type,
                frame,
                Time(seconds=seconds).to_ts("{M:02d}:{S:02d}.{ms:03d}")
            )

        def _handle_source_info( i: int, source: VideoOutputProxy) -> SlowPicsUploadSource:
            name = source.vs_name or f"Node {i}"

            images = 0
            self.format.emit(f"Extracting {name} %v / %m")

            def _progress(a, b):
                nonlocal images
                images += 1
                self.progress.emit(images)


            safe_folder= ("".join(x for x in name if x.isalnum() or x.isspace()))
            if not safe_folder:
                safe_folder = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))

            image_path = (base_path / safe_folder) / f"%0{ndigits(max(frames_n))}d.png"

            image_path.parent.mkdir(parents=True, exist_ok=True)

            clip = self.api.packer.to_rgb_planar(source.vs_output.clip, format=vs.RGB24)
            clip = vs.core.fpng.Write(clip, filename=image_path, compression=1)

            decimated = remap_frames(clip, frames_n)
            image_types = clip_data_gather(decimated, _progress, _frame_callback)

            logger.debug("Saving images to: %s", image_path.parent)

            return SlowPicsUploadSource(name,
                [
                    _handle_image_info(source, frame, image_types[framec], Path(str(image_path)%frame))
                    for framec, frame in enumerate(frames_n)
                ]
            )

        return [
            _handle_source_info(i+1, source) for i, source in enumerate(self.api.voutputs)
        ]


    async def upload_slowpics(self, data: SlowPicsUploadData) -> str:
        """Takes SlowPicsUploadData and uploads to slow.pics based on parameters"""

        is_comparison = len(data.sources) > 1

        total_images = sum(len(source.images) for source in data.sources)

        comp_upload = {}

        for (i, source) in enumerate(data.sources):
            for (j, image) in enumerate(source.images):
                time_str = f"{image.timestamp} / {image.frame_no}"

                if is_comparison:
                    comp_upload[f"comparisons[{j}].name"] = time_str
                    comp_upload[f"comparisons[{j}].imageNames[{i}]"] = f"({image.image_type}) {source.name}"
                else:
                    comp_upload[f"imageNames[{j}]"] = f"{time_str} - {source.name}"

        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20, max_keepalive_connections=5)) as client:

            # TODO: Make version based on dynamic package version
            client.headers.update(get_slowpics_headers())

            # TODO: cookies need loading

            (await client.get("https://slow.pics/comparison")).raise_for_status()

            client.headers.update({
                "X-XSRF-TOKEN": client.cookies.get("XSRF-TOKEN", None),
            })

            # TODO: check if the cookies worked

            browser_id = str(uuid4())

            tags = {}
            if data.info.public:
                tags = {f"tags[{i}]": tag for i, tag in enumerate(data.info.tags)}

            comp_data = (await client.post(
                f"https://slow.pics/upload/{"comparison" if is_comparison else "collection"}",
                data= comp_upload | tags | {
                    "collectionName": data.info.name,
                    "hentai": str(data.info.nsfw).lower(),
                    "optimizeImages": "true",
                    "browserId": browser_id,
                    "public": str(data.info.public).lower(),
                    "removeAfter": data.info.remove_after
                }
            )).raise_for_status().json()

            collection = comp_data["collectionUuid"]
            key = comp_data["key"]
            image_ids = comp_data["images"]

            logger.debug("String upload of: https://slow.pics/c/%s", key)

            reqs = []
            count = 0
            sem = asyncio.Semaphore(5) # How many images to upload at once

            self.range.emit(0, total_images)
            self.format.emit("Uploading images %v / %m")

            for (i, source) in enumerate(data.sources):
                for (j, image) in enumerate(source.images):
                    image_uuid = image_ids[j][i] if is_comparison else image_ids[0][j]

                    async def limited_post(image_uuid:str, image: SlowPicsUploadImage):
                        async with sem:
                            nonlocal count
                            count += 1
                            self.progress.emit(count)
                            return await client.post(f"https://slow.pics/upload/image/{image_uuid}",
                                data={
                                    "collectionUuid": collection,
                                    "imageUuid": image_uuid,
                                    "browserId": browser_id,
                                },
                                files={
                                    "file": (image.path.name, image.path.read_bytes(), "image/png"),
                                }
                            )

                    reqs.append(
                        limited_post(image_uuid, image)
                    )

            await asyncio.gather(*reqs)

            self.format.emit("Finished uploading %v images")

            return f"https://slow.pics/c/{key}"
