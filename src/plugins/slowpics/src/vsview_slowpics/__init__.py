import asyncio
import logging
import webbrowser
from math import copysign
from typing import Annotated, Any

from jetpytools import fallback
from pydantic import BaseModel
from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QSignalBlocker,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from vapoursynth import VideoFrame, VideoNode, core
from vstools import get_prop, scale_mask, stack_planes

from vsview.api import (
    Checkbox,
    LineEdit,
    LocalSettingsModel,
    PluginAPI,
    WidgetPluginBase,
    hookimpl,
)
from vsview.app.settings.models import ActionDefinition, ActionID
from vsview.app.settings.shortcuts import ShortcutManager

from .extract import (
    SlowPicsFramesData,
    SlowPicsImageData,
    SlowPicsUploadData,
    SlowPicsUploadInfo,
    SlowPicsUploadSource,
    SlowPicsWorker,
    SPFrame,
    SPFrameSource,
)
from .panels import FramePopup, TagPopup, TMDBPopup

logger = logging.getLogger("vsview-slowpics")

__version__ = "1.0.0"

class GlobalSettings(BaseModel):
    tmdb_movie_format: Annotated[
        str,
        LineEdit(
            "Format to use when selecting a Movie from TMDB"
        ),
    ] = "{tmdb_title} ({tmdb_year}) - {video_nodes}"
    tmdb_tv_format: Annotated[
        str,
        LineEdit(
            "Format to use when selecting a Movie from TMDB"
        ),
    ] = "{tmdb_title} ({tmdb_year}) - S01E01 - {video_nodes}"
    tmdb_api_key: Annotated[
        str,
        LineEdit(
            "TMDB API Key",
        ),
    ] = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIxYTczNzMzMDE5NjFkMDNmOTdmODUzYTg3NmRkMTIxMiIsInN1YiI6IjU4NjRmNTkyYzNhMzY4MGFiNjAxNzUzNCIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.gh1BwogCCKOda6xj9FRMgAAj_RYKMMPC3oNlcBtlmwk"  # noqa: E501
    p_picttype_default: Annotated[
        bool,
        Checkbox(
            label="P PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    b_picttype_default: Annotated[
        bool,
        Checkbox(
            label="B PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    i_picttype_default: Annotated[
        bool,
        Checkbox(
            label="I PictType Default",
            text="",
            tooltip="If checked it will enable this PictType by default.",
        ),
    ] = True
    current_frame_default: Annotated[
        bool,
        Checkbox(
            label="Current Frame Default",
            text="",
            tooltip="If checked it will enable current frame by default.",
        ),
    ] = True
    public_comp_default: Annotated[
        bool,
        Checkbox(
            label="Public Comp Default",
            text="",
            tooltip="If checked it will enable public comps by default.",
        ),
    ] = True
    open_comp_automatically: Annotated[
        bool,
        Checkbox(
            label="Open comp links automatically",
            text="",
            tooltip="Will open the link to the comp once it has finished automatically.",
        ),
    ] = False

class LocalSettings(LocalSettingsModel):
    pass


class SlowPicsPlugin(WidgetPluginBase[GlobalSettings, LocalSettings]):
    identifier = "jet_vsview_slowpics"
    display_name = "Slow.pics Uploader"

    shortcuts = (ActionDefinition("jet_vsview_slowpics.add_current_frame", "Add Current Frame", "Shift+Space"),)

    start_job = Signal(str, object, bool)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        self.tmdb: dict[str, Any] = {}
        self.tags: list[str] = []
        self.extracted_sources: list[SlowPicsUploadSource] = []
        self.frames: list[SPFrame] = []
        self.manual_frames: set[SPFrame] = set()

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.poster_container = QWidget()
        poster_layout = QHBoxLayout(self.poster_container)

        self.poster_label = QLabel()
        self.poster_label.setFixedSize(98, 138)
        self.poster_label.setStyleSheet("background-color: #444; border: 1px solid #222;")

        self.show_name_label = QLabel("Show Name")
        self.show_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        poster_layout.addWidget(self.poster_label)
        poster_layout.addWidget(self.show_name_label)

        self.poster_container.setVisible(False)
        main_layout.addWidget(self.poster_container)

        self.comp_title = QLineEdit()
        self.comp_title.setPlaceholderText("Comp Title")
        main_layout.addWidget(self.comp_title)

        main_layout.addWidget(QLabel("Current Frames"))
        current_frames_row = QHBoxLayout()

        self.frames_dropdown = QComboBox()
        self.frames_dropdown.currentIndexChanged.connect(self._frame_selected)

        self.add_manual_frame_btn = QPushButton("+")
        self.add_manual_frame_btn.clicked.connect(self.handle_frame_ui)
        self.add_manual_frame_btn.setFixedWidth(28)
        self.add_manual_frame_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        current_frames_row.addWidget(self.frames_dropdown)
        current_frames_row.addWidget(self.add_manual_frame_btn)

        main_layout.addLayout(current_frames_row)

        pictype_layout = QVBoxLayout()
        pictype_layout.addWidget(QLabel("Picture Types"))
        pictype_row = QHBoxLayout()
        self.i_frame = QCheckBox("I")
        self.p_frame = QCheckBox("P")
        self.b_frame = QCheckBox("B")
        self.i_frame.setChecked(self.settings.global_.i_picttype_default)
        self.p_frame.setChecked(self.settings.global_.p_picttype_default)
        self.b_frame.setChecked(self.settings.global_.b_picttype_default)
        pictype_row.addWidget(self.i_frame)
        pictype_row.addWidget(self.p_frame)
        pictype_row.addWidget(self.b_frame)
        pictype_layout.addLayout(pictype_row)
        main_layout.addLayout(pictype_layout)


        pubnsfw_row = QHBoxLayout()
        self.public_check = QCheckBox("Public")
        self.public_check.setChecked(self.settings.global_.public_comp_default)
        self.nsfw_check = QCheckBox("NSFW")
        self.current_frame_check = QCheckBox("Include Current Frame")
        self.current_frame_check.setChecked(self.settings.global_.current_frame_default)
        pubnsfw_row.addWidget(self.public_check)
        pubnsfw_row.addWidget(self.nsfw_check)
        pubnsfw_row.addWidget(self.current_frame_check)
        main_layout.addLayout(pubnsfw_row)



        random_remove_layout = QHBoxLayout()

        random_layout = QVBoxLayout()
        random_layout.addWidget(QLabel("Random Frame Count"))
        self.random_frames = QSpinBox()
        self.random_frames.setRange(0, 70)
        random_layout.addWidget(self.random_frames)

        remove_layout = QVBoxLayout()
        remove_layout.addWidget(QLabel("Remove After N days"))
        self.remove_after = QSpinBox()
        self.remove_after.setRange(0, 999999)
        remove_layout.addWidget(self.remove_after)

        random_remove_layout.addLayout(random_layout)
        random_remove_layout.addLayout(remove_layout)
        main_layout.addLayout(random_remove_layout)

        light_dark_layout = QHBoxLayout()

        light_layout = QVBoxLayout()
        light_layout.addWidget(QLabel("Light Frames"))
        self.light_frames = QSpinBox()
        self.light_frames.setRange(0, 10)
        light_layout.addWidget(self.light_frames)

        dark_layout = QVBoxLayout()
        dark_layout.addWidget(QLabel("Dark Frames"))
        self.dark_frames = QSpinBox()
        self.dark_frames.setRange(0, 10)
        dark_layout.addWidget(self.dark_frames)

        light_dark_layout.addLayout(light_layout)
        light_dark_layout.addLayout(dark_layout)
        main_layout.addLayout(light_dark_layout)

        meta_tag_row = QHBoxLayout()
        self.metadata_btn = QPushButton("Search TMDB")
        self.metadata_btn.clicked.connect(self._open_tmdb_search_popup)
        self.tags_btn = QPushButton("Select Tags")
        self.tags_btn.clicked.connect(self._open_tag_menu)

        meta_tag_row.addWidget(self.metadata_btn)
        meta_tag_row.addWidget(self.tags_btn)
        main_layout.addLayout(meta_tag_row)

        action_row = QHBoxLayout()
        self.get_frames_btn = QPushButton("Get Frames")
        self.extract_frames_btn = QPushButton("Extract Frames")
        self.upload_images_btn = QPushButton("Upload Images")
        self.get_frames_btn.clicked.connect(lambda a: self.do_job("frames"))
        self.extract_frames_btn.clicked.connect(lambda a: self.do_job("extract"))
        self.upload_images_btn.clicked.connect(lambda a: self.do_job("upload"))
        action_row.addWidget(self.get_frames_btn)
        action_row.addWidget(self.extract_frames_btn)
        action_row.addWidget(self.upload_images_btn)
        main_layout.addLayout(action_row)

        self.do_all_btn = QPushButton("Do All 3")
        self.do_all_btn.clicked.connect(lambda a: self.do_job("frames", True))
        main_layout.addWidget(self.do_all_btn)

        # main_layout.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)

        main_layout.addWidget(self.progress_bar)

        self.init_worker()
        self._setup_shortcuts()


    def _setup_shortcuts(self) -> None:
        self.api.register_shortcut(
            "jet_vsview_slowpics.add_current_frame",
            lambda: self.add_manual_frame(self.api.current_frame),
            self,
            context=Qt.ShortcutContext.WindowShortcut
        )


    def on_current_frame_changed(self, n: int) -> None:
        pass

    def init_worker(self) -> None:
        self.thread_handle = QThread()
        self.worker = SlowPicsWorker()
        self.worker.setApi(self.api)

        self.worker.moveToThread(self.thread_handle)

        self.thread_handle.start()

        self.worker.format.connect(self.progress_bar.setFormat)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.range.connect(self.progress_bar.setRange)
        self.worker.finished.connect(self.handle_finish)

        self.start_job.connect(self.worker.do_work)

        self.api.register_on_destroy(self.kill_worker)

        self.net = QNetworkAccessManager(self)
        self.net.finished.connect(self.on_icon_downloaded)


    def do_job(self, job_name:str, do_next:bool=False) -> None:
        if job_name == "frames":
            pict_types = set()
            if self.p_frame.isChecked():
                pict_types.add("P")
            if self.b_frame.isChecked():
                pict_types.add("B")
            if self.i_frame.isChecked():
                pict_types.add("I")

            data = SlowPicsFramesData(
                int(self.random_frames.value()),
                0,
                None,
                self.dark_frames.value(),
                self.light_frames.value(),
                pict_types,
                self.current_frame_check.isChecked()
            )

            self.extracted_sources = []
            self.frames = []

            self.start_job.emit("frames", data, do_next)
        elif job_name == "extract":
            if not self.frames:
                logger.debug("Trying to extract with no frames.")
                return

            plugin_path = self.api.get_local_storage(self)

            if not plugin_path:
                logger.debug("No plugin path")
                return

            extract = SlowPicsImageData(plugin_path, self.frames)
            self.extracted_sources = []
            self.start_job.emit("extract", extract, do_next)

        elif job_name == "upload":
            if not self.extracted_sources:
                logger.debug("Trying to upload without any images")
                return

            upload_data = SlowPicsUploadInfo(
                self.comp_title.text(),
                self.public_check.isChecked(),
                self.nsfw_check.isChecked(),
                self.tmdb.get("id", None),
                self.remove_after.value(),
                self.tags
            )
            upload = SlowPicsUploadData(upload_data, self.extracted_sources)
            self.start_job.emit("upload", upload, do_next)

    def handle_finish(self, job_name:str, result: Any, do_next:bool) -> None:
        # print(job_name, result)

        if job_name == "frames":
            self.frames = result
            self.add_frames()
        elif job_name == "extract":
            # self.start_job.emit("upload", result)
            self.extracted_sources = result
        elif job_name == "upload":
            logger.debug("Uploaded comp: %s", result)
            if self.settings.global_.open_comp_automatically:
                webbrowser.open(result)

        self.handle_do_next(job_name, do_next)

    def handle_do_next(self, job_name:str, do_next:bool) -> None:
        # Do next job if clicked all 3
        if not do_next:
            return

        if job_name == "frames":
            self.do_job("extract", True)
        elif job_name == "extract":
            self.do_job("upload", True)


    def kill_worker(self) -> None:
        if self.thread_handle.isRunning():
            self.thread_handle.quit()
            self.thread_handle.wait()

    def add_frames(self) -> None:
        self.frames_dropdown.clear()
        frames: list[SPFrame] = sorted(self.frames + list(self.manual_frames), key=lambda x: x.frame)
        for frame in frames:
            self.frames_dropdown.addItem(f"{frame.frame} {frame.frame_type}", frame)


    def _frame_selected(self, index:int) -> None:
        # data: SPFrame = self.frames_dropdown.itemData(index)

        # self.api.__workspace._seek_frame(data.frame)
        pass

    def _open_tmdb_search_popup(self) -> None:
        self.popup = TMDBPopup(self, self.settings.global_.tmdb_api_key)
        self.popup.item_selected.connect(self._handle_tmdb_selected)
        self.popup.exec()
        self.popup.search_input.setFocus()

    def _handle_tmdb_selected(self, data: dict[str, Any]) -> None:
        self.tmdb = data

        self.handle_comp_title()


    def handle_comp_title(self) -> None:
        is_tv = self.tmdb["is_tv"]
        result = self.tmdb["result"]

        if is_tv:
            comp_title = self.settings.global_.tmdb_tv_format
            comp_title = comp_title.replace("{tmdb_title}", result["name"])
            comp_title = comp_title.replace("{tmdb_year}", (result["first_air_date"] or "0000")[:4])
        else:
            comp_title = self.settings.global_.tmdb_movie_format
            comp_title = comp_title.replace("{tmdb_title}", result["title"])
            comp_title = comp_title.replace("{tmdb_year}", (result["release_date"] or "0000")[:4])


        comp_title = comp_title.replace(
            "{video_nodes}",
            " vs ".join([source.vs_name or f"Node {i+1}" for i, source in enumerate(self.api.voutputs)])
        )

        self.comp_title.setText(comp_title)
        if result["poster_path"]:
            request = QNetworkRequest(QUrl(f"https://image.tmdb.org/t/p/w92{result["poster_path"]}"))
            self.net.get(request)

    def on_icon_downloaded(self, reply: QNetworkReply) -> None:

        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return

        data = reply.readAll()
        pixmap = QPixmap()
        pixmap.loadFromData(data)

        if not pixmap.isNull():
            self.poster_label.setPixmap(pixmap)
            is_tv = self.tmdb["is_tv"]
            result = self.tmdb["result"]
            self.show_name_label.setText(result["name"] if is_tv else result["title"])
            self.poster_container.setVisible(True)

        reply.deleteLater()

    def _open_tag_menu(self) -> None:
        self.tag_popup = TagPopup(self, self.tags)
        self.tag_popup.item_selected.connect(self.handle_tag_selection)
        self.tag_popup.exec()
        self.tag_popup.search.setFocus()

    def handle_tag_selection(self, tags: list[str]) -> None:
        self.tags = tags

    def add_manual_frame(self, frame:int) -> None:
        mframe = SPFrame(frame, SPFrameSource.MANUAL)

        if mframe in self.manual_frames:
            self.manual_frames.remove(mframe)
        else:
            self.manual_frames.add(mframe)

        self.add_frames()

    def handle_frame_ui(self, checked:bool) -> None:
        dialog = FramePopup(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            for frame in dialog.frames:
                self.manual_frames.add(SPFrame(frame, SPFrameSource.MANUAL))
            self.add_frames()



@hookimpl
def vsview_register_toolpanel() -> type[WidgetPluginBase[Any, Any]]:
    return SlowPicsPlugin


# @hookimpl
# def vsview_register_tooldock() -> type[WidgetPluginBase[Any, Any]]:
#     return SlowPicsPlugin
