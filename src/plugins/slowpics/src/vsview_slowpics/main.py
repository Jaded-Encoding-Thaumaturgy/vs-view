import logging
import webbrowser
from typing import Any

from PySide6.QtCore import (
    Qt,
    QThread,
    QUrl,
    Signal,
)
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
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

from vsview.api import (
    IconReloadMixin,
    PluginAPI,
    WidgetPluginBase,
)
from vsview.app.settings.models import ActionDefinition
from vsview.assets.providers import IconName

from .panels import FramePopup, LoginPopup, TagPopup, TMDBPopup
from .settings import GlobalSettings
from .worker import (
    SlowPicsFramesData,
    SlowPicsImageData,
    SlowPicsUploadData,
    SlowPicsUploadInfo,
    SlowPicsUploadSource,
    SlowPicsWorker,
    SPFrame,
    SPFrameSource,
)

logger = logging.getLogger(__name__)


class SlowPicsPlugin(WidgetPluginBase[GlobalSettings], IconReloadMixin):
    identifier = "jet_vsview_slowpics"
    display_name = "Slow.pics Tool"

    shortcuts = (ActionDefinition("jet_vsview_slowpics.add_current_frame", "Add Current Frame", "Shift+Space"),)

    startJob = Signal(str, object, bool)
    sendSettings = Signal(object)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        self.tmdb_info = dict[str, Any]()
        self.selected_tags = list[str]()
        self.extracted_sources = list[SlowPicsUploadSource]()
        self.frames = list[SPFrame]()
        self.manual_frames = set[SPFrame]()

        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.poster_container = QWidget(self)
        poster_layout = QHBoxLayout(self.poster_container)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        self.poster_label = QLabel(self.poster_container)
        self.poster_label.setFixedSize(98, 138)
        self.poster_label.setStyleSheet("background-color: #444; border: 1px solid #222;")

        self.show_name_label = QLabel("Show Name", self.poster_container)
        self.show_name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        poster_layout.addWidget(self.poster_label)
        poster_layout.addWidget(self.show_name_label)

        self.poster_container.setVisible(False)
        main_layout.addWidget(self.poster_container)

        self.comp_title = QLineEdit(self)
        self.comp_title.setPlaceholderText("Comp Title")
        main_layout.addWidget(self.comp_title)

        main_layout.addWidget(QLabel("Current Frames"))
        frame_widget = QWidget()
        current_frames_row = QHBoxLayout(frame_widget)
        current_frames_row.setContentsMargins(0, 0, 0, 0)
        self.frames_dropdown = QComboBox(frame_widget)
        self.frames_dropdown.currentIndexChanged.connect(self._frame_selected)

        self.remove_manual_frame_btn = self.make_tool_button(
            IconName.MINUS, "Remove the current selected frame", frame_widget
        )
        self.remove_manual_frame_btn.clicked.connect(self.remove_frame)
        self.remove_manual_frame_btn.setFixedWidth(28)
        self.remove_manual_frame_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.add_manual_frame_btn = self.make_tool_button(
            IconName.PLUS, "Open a dialog to add frames manually", frame_widget
        )
        self.add_manual_frame_btn.clicked.connect(self.handle_frame_ui)
        self.add_manual_frame_btn.setFixedWidth(28)
        self.add_manual_frame_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        current_frames_row.addWidget(self.frames_dropdown)
        current_frames_row.addWidget(self.remove_manual_frame_btn)
        current_frames_row.addWidget(self.add_manual_frame_btn)

        main_layout.addWidget(frame_widget)

        pic_widget = QWidget(self)
        pic_widget.setContentsMargins(0, 0, 0, 0)
        pictype_layout = QVBoxLayout(pic_widget)
        pictype_layout.setContentsMargins(0, 0, 0, 0)
        pictype_layout.addWidget(QLabel("Picture Types"))

        pictype_widget = QWidget(self)
        pictype_row = QHBoxLayout(pictype_widget)
        pictype_row.setContentsMargins(0, 0, 0, 0)
        self.i_frame = QCheckBox("I", pictype_widget)
        self.p_frame = QCheckBox("P", pictype_widget)
        self.b_frame = QCheckBox("B", pictype_widget)
        self.i_frame.setChecked(self.settings.global_.i_picttype_default)
        self.p_frame.setChecked(self.settings.global_.p_picttype_default)
        self.b_frame.setChecked(self.settings.global_.b_picttype_default)
        pictype_row.addWidget(self.i_frame)
        pictype_row.addWidget(self.p_frame)
        pictype_row.addWidget(self.b_frame)
        pictype_layout.addWidget(pictype_widget)
        main_layout.addWidget(pic_widget)

        pubnsfw_widget = QWidget(self)
        pubnsfw_row = QHBoxLayout(pubnsfw_widget)
        pubnsfw_row.setContentsMargins(0, 0, 0, 0)
        self.public_check = QCheckBox("Public", pubnsfw_widget)
        self.public_check.setChecked(self.settings.global_.public_comp_default)
        self.nsfw_check = QCheckBox("NSFW", pubnsfw_widget)
        self.current_frame_check = QCheckBox("Include Current Frame", pubnsfw_widget)
        self.current_frame_check.setChecked(self.settings.global_.current_frame_default)
        pubnsfw_row.addWidget(self.public_check)
        pubnsfw_row.addWidget(self.nsfw_check)
        pubnsfw_row.addWidget(self.current_frame_check)
        main_layout.addWidget(pubnsfw_widget)

        random_remove_widget = QWidget(self)
        random_remove_layout = QHBoxLayout(random_remove_widget)
        random_remove_layout.setContentsMargins(0, 0, 0, 0)
        random_widget = QWidget(random_remove_widget)
        random_layout = QVBoxLayout(random_widget)
        random_layout.setContentsMargins(0, 0, 0, 0)
        random_layout.addWidget(QLabel("Random Frame Count", random_widget))
        self.random_frames = QSpinBox(random_widget)
        self.random_frames.setRange(0, 70)
        random_layout.addWidget(self.random_frames)

        remove_widget = QWidget(self)
        remove_layout = QVBoxLayout(remove_widget)
        remove_layout.setContentsMargins(0, 0, 0, 0)
        remove_layout.addWidget(QLabel("Remove After N days", remove_widget))
        self.remove_after = QSpinBox(remove_widget)
        self.remove_after.setRange(0, 999999)
        remove_layout.addWidget(self.remove_after)

        random_remove_layout.addWidget(random_widget)
        random_remove_layout.addWidget(remove_widget)
        main_layout.addWidget(random_remove_widget)

        light_dark_widget = QWidget(self)
        light_dark_layout = QHBoxLayout(light_dark_widget)
        light_dark_layout.setContentsMargins(0, 0, 0, 0)

        light_widget = QWidget(self)
        light_layout = QVBoxLayout(light_widget)
        light_layout.setContentsMargins(0, 0, 0, 0)
        light_layout.addWidget(QLabel("Light Frames", light_widget))
        self.light_frames = QSpinBox(light_widget)
        self.light_frames.setRange(0, 10)
        light_layout.addWidget(self.light_frames)

        dark_widget = QWidget(self)
        dark_layout = QVBoxLayout(dark_widget)
        dark_layout.setContentsMargins(0, 0, 0, 0)
        dark_layout.addWidget(QLabel("Dark Frames", dark_widget))
        self.dark_frames = QSpinBox(dark_widget)
        self.dark_frames.setRange(0, 10)
        dark_layout.addWidget(self.dark_frames)

        light_dark_layout.addWidget(light_widget)
        light_dark_layout.addWidget(dark_widget)
        main_layout.addWidget(light_dark_widget)

        meta_tag_widget = QWidget(self)
        meta_tag_row = QHBoxLayout(meta_tag_widget)
        meta_tag_row.setContentsMargins(0, 0, 0, 0)
        self.metadata_btn = QPushButton("Search TMDB", meta_tag_widget)
        self.metadata_btn.clicked.connect(self._open_tmdb_search_popup)
        self.tags_btn = QPushButton("Select Tags", meta_tag_widget)
        self.tags_btn.clicked.connect(self._open_tag_menu)
        self.login_btn = QPushButton("Login to Slow.pics", meta_tag_widget)
        self.login_btn.clicked.connect(self._login_to_slowpics)

        meta_tag_row.addWidget(self.metadata_btn)
        meta_tag_row.addWidget(self.tags_btn)
        meta_tag_row.addWidget(self.login_btn)
        main_layout.addWidget(meta_tag_widget)

        action_widget = QWidget(self)
        action_row = QHBoxLayout(action_widget)
        action_row.setContentsMargins(0, 0, 0, 0)
        self.get_frames_btn = QPushButton("Get Frames", action_widget)
        self.extract_frames_btn = QPushButton("Extract Frames", action_widget)
        self.upload_images_btn = QPushButton("Upload Images", action_widget)
        self.get_frames_btn.clicked.connect(lambda: self.do_job("frames"))
        self.extract_frames_btn.clicked.connect(lambda: self.do_job("extract"))
        self.upload_images_btn.clicked.connect(lambda: self.do_job("upload"))
        action_row.addWidget(self.get_frames_btn)
        action_row.addWidget(self.extract_frames_btn)
        action_row.addWidget(self.upload_images_btn)
        main_layout.addWidget(action_widget)

        self.do_all_btn = QPushButton("All 3", self)
        self.do_all_btn.clicked.connect(lambda: self.do_job("frames", True))
        main_layout.addWidget(self.do_all_btn)

        # main_layout.addStretch()

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setHidden(True)

        main_layout.addWidget(self.progress_bar)

        self.api.globalSettingsChanged.connect(self.on_settings_changed)

        self.init_worker()
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        self.api.register_shortcut(
            "jet_vsview_slowpics.add_current_frame",
            lambda: self.add_manual_frame(self.api.current_frame),
            self,
            context=Qt.ShortcutContext.WindowShortcut,
        )

    def on_current_frame_changed(self, n: int) -> None:
        pass

    def init_worker(self) -> None:
        self.thread_handle = QThread()
        self.worker = SlowPicsWorker(self.api, self.settings.global_)
        self.worker.moveToThread(self.thread_handle)

        self.thread_handle.start()

        self.worker.progressFormat.connect(self.progress_bar.setFormat)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.progressRange.connect(self.progress_bar.setRange)
        self.worker.jobFinished.connect(self.handle_finish)
        self.worker.updateSettings.connect(self.worker_update_settings)

        self.startJob.connect(self.worker.do_work)
        self.sendSettings.connect(self.worker.update_settings)

        self.api.register_on_destroy(self.kill_worker)

        self.net = QNetworkAccessManager(self)
        self.net.finished.connect(self.on_icon_downloaded)

    def do_job(self, job_name: str, do_next: bool = False) -> None:
        self.progress_bar.setHidden(False)
        if job_name == "frames":
            self.do_get_frames(do_next)
        elif job_name == "extract":
            self.do_extract_frames(do_next)
        elif job_name == "upload":
            self.do_upload_images(do_next)

    def do_get_frames(self, do_next: bool) -> None:
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
            self.current_frame_check.isChecked(),
        )

        self.extracted_sources = []
        self.frames = []

        self.startJob.emit("frames", data, do_next)

    def do_extract_frames(self, do_next: bool) -> None:
        if not self.frames:
            logger.debug("Trying to extract with no frames.")
            return

        plugin_path = self.api.get_local_storage(self)

        if not plugin_path:
            logger.debug("No plugin path")
            return

        extract = SlowPicsImageData(plugin_path, self.frames)
        self.extracted_sources = []
        self.startJob.emit("extract", extract, do_next)

    def do_upload_images(self, do_next: bool) -> None:
        if not self.extracted_sources:
            logger.debug("Trying to upload without any images")
            return

        upload_data = SlowPicsUploadInfo(
            self.comp_title.text(),
            self.public_check.isChecked(),
            self.nsfw_check.isChecked(),
            self.tmdb_info.get("id", None),
            self.remove_after.value(),
            self.selected_tags,
        )
        upload = SlowPicsUploadData(upload_data, self.extracted_sources)
        self.startJob.emit("upload", upload, do_next)

    def handle_finish(self, job_name: str, result: Any, do_next: bool) -> None:
        if job_name == "frames":
            self.handle_get_frames(result)
        elif job_name == "extract":
            self.handle_extract_frames(result)
        elif job_name == "upload":
            self.handle_upload_images(result)

        self.handle_do_next(job_name, do_next)

    def handle_get_frames(self, result: list[SPFrame]) -> None:
        self.frames = result
        self.add_frames()

    def handle_extract_frames(self, result: list[SlowPicsUploadSource]) -> None:
        self.extracted_sources = result

    def handle_upload_images(self, result: str) -> None:
        logger.debug("Uploaded comp: %s", result)
        if self.settings.global_.open_comp_automatically:
            webbrowser.open(result)

    def handle_do_next(self, job_name: str, do_next: bool) -> None:
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
        self.frames_dropdown.blockSignals(True)
        self.frames_dropdown.clear()
        frames: list[SPFrame] = sorted(self.frames + list(self.manual_frames), key=lambda x: x.frame)
        for frame in frames:
            self.frames_dropdown.addItem(f"{frame.frame} ({frame.frame_type.name})", frame)
        self.frames_dropdown.blockSignals(False)

    def _frame_selected(self, index: int) -> None:
        data: SPFrame = self.frames_dropdown.itemData(index)

        self.api.playback.seek(data.frame)

    def _open_tmdb_search_popup(self) -> None:
        self.popup = TMDBPopup(self, self.settings.global_.tmdb_api_key)
        self.popup.itemSelected.connect(self._handle_tmdb_selected)
        self.popup.exec()
        self.popup.search_input.setFocus()

    def _handle_tmdb_selected(self, data: dict[str, Any]) -> None:
        self.tmdb_info = data

        self.handle_comp_title()

    def handle_comp_title(self) -> None:
        is_tv = self.tmdb_info["is_tv"]
        result = self.tmdb_info["result"]

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
            " vs ".join([source.vs_name or f"Node {source.vs_index}" for source in self.api.voutputs]),
        )

        self.comp_title.setText(comp_title)
        if result["poster_path"]:
            request = QNetworkRequest(QUrl(f"https://image.tmdb.org/t/p/w92{result['poster_path']}"))
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
            is_tv = self.tmdb_info["is_tv"]
            result = self.tmdb_info["result"]
            self.show_name_label.setText(result["name"] if is_tv else result["title"])
            self.poster_container.setVisible(True)

        reply.deleteLater()

    def _open_tag_menu(self) -> None:
        self.tag_popup = TagPopup(self, self.selected_tags)
        self.tag_popup.itemSelected.connect(self.handle_tag_selection)
        self.tag_popup.exec()
        self.tag_popup.search.setFocus()

    def handle_tag_selection(self, tags: list[str]) -> None:
        self.selected_tags = tags

    def add_manual_frame(self, frame: int) -> None:
        mframe = SPFrame(frame, SPFrameSource.MANUAL)

        if mframe in self.manual_frames:
            self.manual_frames.remove(mframe)
        else:
            self.manual_frames.add(mframe)

        self.add_frames()

    def handle_frame_ui(self, checked: bool) -> None:
        dialog = FramePopup(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            for frame in dialog.frames:
                if frame <= self.api.current_voutput.vs_output.clip.num_frames and frame >= 0:
                    self.manual_frames.add(SPFrame(frame, SPFrameSource.MANUAL))
            self.add_frames()

    def remove_frame(self) -> None:
        if self.frames_dropdown.count() == 0:
            return

        current_data: SPFrame = self.frames_dropdown.currentData()
        if current_data.frame_type == SPFrameSource.MANUAL:
            self.manual_frames.remove(current_data)
        else:
            self.frames.remove(current_data)

        self.add_frames()

    def _login_to_slowpics(self) -> None:
        dialog = LoginPopup(self)
        dialog.username_edit.setFocus()

        if dialog.exec() == QDialog.DialogCode.Accepted:
            logger.debug("Attempting to login")
            login_data = {"username": dialog.username, "password": dialog.password}

            self.startJob.emit("login", login_data, False)

    def on_settings_changed(self) -> None:
        self.sendSettings.emit(self.settings.global_)

    def worker_update_settings(self, to_change: dict[str, Any]) -> None:
        self.update_global_settings(cookies=to_change)

        # send this one as a temp for the real one to be sent above.
        self.settings.global_.cookies = to_change
        self.sendSettings.emit(self.settings.global_)
