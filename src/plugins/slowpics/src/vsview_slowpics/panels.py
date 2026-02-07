import logging
from typing import Any

import httpx
from PySide6.QtCore import (
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .utils import get_slowpics_headers

logger = logging.getLogger("vsview-slowpics")


class TMDBPopup(QDialog):
    itemSelected = Signal(object)

    def __init__(self, parent: QWidget, api_key: str) -> None:
        super().__init__(parent)

        self.setWindowTitle("Search")
        self.resize(500, 500)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to search...")
        layout.addWidget(self.search_input)

        self.status_label = QLabel("Start typing…")
        layout.addWidget(self.status_label)

        self.results_list = QListWidget()
        self.results_list.setIconSize(QSize(32, 45))
        layout.addWidget(self.results_list)

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(300)

        self.search_input.textChanged.connect(self.on_text_changed)
        self.debounce_timer.timeout.connect(self.perform_search)
        self.results_list.itemActivated.connect(self.handle_item_selected)

        self.net = QNetworkAccessManager(self)
        self.net.finished.connect(self.on_icon_downloaded)
        self._icon_requests: dict[QNetworkReply, Any] = {}

        self.client = httpx.Client()
        self.client.headers.update({"Authorization": f"Bearer {api_key}"})

        movieg = self.client.get("https://api.themoviedb.org/3/genre/movie/list").raise_for_status().json()
        self.movie_genre = {genre["id"]: genre["name"] for genre in movieg["genres"]}
        tvg = self.client.get("https://api.themoviedb.org/3/genre/tv/list").raise_for_status().json()
        self.tv_genre = {genre["id"]: genre["name"] for genre in tvg["genres"]}

    def on_text_changed(self, text: str) -> None:
        self.status_label.setText("Typing…")
        self.debounce_timer.start()

    def perform_search(self) -> None:
        query = self.search_input.text().strip()
        self.results_list.clear()

        if not query:
            self.status_label.setText("Start typing…")
            return

        self.status_label.setText(f"Searching for: {query}")

        tv = (
            self.client.get(
                "https://api.themoviedb.org/3/search/tv",
                params={"include_adult": "false", "query": query, "language": "en-US"},
            )
            .raise_for_status()
            .json()
        )

        self.add_response(tv, True)

        movie = (
            self.client.get(
                "https://api.themoviedb.org/3/search/movie",
                params={"include_adult": "false", "query": query, "language": "en-US"},
            )
            .raise_for_status()
            .json()
        )
        self.add_response(movie, False)

        if query.isnumeric():
            try:
                tv = (
                    self.client.get(
                        f"https://api.themoviedb.org/3/tv/{query}",
                        params={"include_adult": "false", "language": "en-US"},
                    )
                    .raise_for_status()
                    .json()
                )

                self.add_response(tv, True, False)
            except Exception as e:
                logger.error(e)
                logger.debug("Full traceback",  exc_info=True)

            try:
                movie = (
                    self.client.get(
                        f"https://api.themoviedb.org/3/movie/{query}",
                        params={"include_adult": "false", "language": "en-US"},
                    )
                    .raise_for_status()
                    .json()
                )

                self.add_response(movie, False, False)
            except Exception as e:
                logger.error(e)
                logger.debug("Full traceback",  exc_info=True)

    def add_response(self, data: dict[str, Any], is_tv: bool, is_list: bool = True) -> None:

        values = []
        for result in data["results"] if is_list else [data]:
            label = ""
            if is_tv:
                label += f"{result['name']} [{(result['first_air_date'] or '0000')[:4]}] [TV]"
            else:
                label += f"{result['title']} [{(result['release_date'] or '0000')[:4]}] [MOVIE]"

            if is_list:
                genres = self.tv_genre if is_tv else self.movie_genre
                label += f" [{
                    ', '.join([genres[genre] for genre in result['genre_ids']])
                }]"
            else:
                label += f" [{', '.join([genre['name'] for genre in result['genres']])}]"

            values.append({"label": label, "result": result, "is_tv": is_tv})

        for value in values:
            item = QListWidgetItem(value["label"])

            item.setData(Qt.ItemDataRole.UserRole, value)

            self.results_list.addItem(item)

            if value["result"]["poster_path"]:
                request = QNetworkRequest(QUrl(f"https://image.tmdb.org/t/p/w92{value['result']['poster_path']}"))
                reply = self.net.get(request)
                self._icon_requests[reply] = item

    def on_icon_downloaded(self, reply: QNetworkReply) -> None:
        item: QListWidgetItem = self._icon_requests.pop(reply, None)
        if not item:
            reply.deleteLater()
            return

        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return

        data = reply.readAll()
        pixmap = QPixmap()
        pixmap.loadFromData(data)

        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.results_list.iconSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon = QIcon(scaled)
            item.setIcon(icon)

        reply.deleteLater()

    def handle_item_selected(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        self.itemSelected.emit(data)
        self.close()


class TagPopup(QDialog):
    itemSelected = Signal(object)

    def __init__(self, parent: QWidget, selected: list[str]) -> None:
        super().__init__(parent)

        self.setWindowTitle("Tag Selection")
        self.resize(500, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.search.textChanged.connect(self.filter_items)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.itemChanged.connect(self.get_checked_items)
        layout.addWidget(self.list_widget)

        self.client = httpx.Client()
        self.client.headers.update(get_slowpics_headers())

        self.tags = self.client.get("https://slow.pics/api/tags").raise_for_status().json()

        self.selected = selected

        self.populate_list()

    def populate_list(self) -> None:
        self.list_widget.clear()
        for tag in self.tags:
            item = QListWidgetItem(tag["label"])
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if tag["value"] in self.selected else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)

    def filter_items(self, text: str) -> None:
        text = text.lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text not in item.text().lower())

    def get_checked_items(self) -> None:
        checked = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked.append(item.data(Qt.ItemDataRole.UserRole)["value"])
        self.itemSelected.emit(checked)


class FramePopup(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enter Frames")

        self.vlayout = QVBoxLayout(self)

        self.label = QLabel("Enter frame numbers (comma separated):")
        self.vlayout.addWidget(self.label)

        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("e.g. 1, 5, 10, 42")
        self.vlayout.addWidget(self.line_edit)

        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.validate_and_accept)
        self.vlayout.addWidget(self.ok_button)

        self.frames: list[int] = []

    def validate_and_accept(self) -> None:
        text = self.line_edit.text().strip()

        if not text:
            QMessageBox.warning(self, "Input Error", "Please enter at least one frame.")
            return

        try:
            # Convert comma-separated string into a list of ints
            frames = [int(f.strip()) for f in text.split(",") if f.strip()]
            self.frames = frames
            self.accept()
        except ValueError:
            QMessageBox.critical(self, "Invalid Input", "Frames must be comma-separated integers (e.g. 1, 5, 10).")
