from __future__ import annotations

import threading
from collections.abc import Generator, Sequence
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from logging import getLogger
from pathlib import Path
from typing import Any, NamedTuple, Self, override
from uuid import uuid4

import jinja2
from jetpytools import cachedproperty
from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QIcon, QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPixmap, QWheelEvent
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    QWidgetItem,
)
from shiboken6 import Shiboken
from vapoursynth import VideoFrame, VideoNode
from vstools import core, get_prop

from vsview.api import (
    IconName,
    IconReloadMixin,
    LineEdit,
    NonClosingMenu,
    PluginAPI,
    Time,
    VideoOutputProxy,
    WidgetMetadata,
    run_in_background,
    run_in_loop,
)

from .models import TMDBTitle

logger = getLogger(__name__)


class MainCompWidget(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        self.container = QWidget(self)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 8, 8, 8)
        self.container_layout.setSpacing(8)

        self.setWidget(self.container)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.finalize()

    def add_section(self, section: QWidget) -> None:
        self.container_layout.addWidget(section)

    def finalize(self) -> None:
        self.container_layout.addStretch(1)


class PaddedCheckBox(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Include this output")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.checkbox = QCheckBox(self)
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.checkbox.toggled.connect(self.toggled.emit)
        layout.addWidget(self.checkbox)

    def setChecked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)

    def isChecked(self) -> bool:
        return self.checkbox.isChecked()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.toggle()
            event.accept()
            return

        super().mouseReleaseEvent(event)


class OutputItemWidget(QWidget):
    inclusionChanged = Signal(bool)
    nameChanged = Signal(str)

    def __init__(self, vout: VideoOutputProxy, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.included = True
        self.vs_index = vout.vs_index
        self.vs_name = vout.vs_name
        self.num_frames = vout.vs_output.clip.num_frames
        self.duration = vout.info.total_duration

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)

        self.checkbox = PaddedCheckBox(self)
        self.checkbox.setChecked(True)
        self.checkbox.toggled.connect(self._on_check)

        self.name_edit = QLineEdit(self.vs_name, self)
        self.name_edit.textChanged.connect(self._on_name)

        self.info_label = QLabel(f"{self.duration.to_ts('{H:01d}:{M:02d}:{S:02d}.{ms:03d}')} ({self.num_frames})", self)

        layout.addWidget(self.checkbox)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.info_label)

    # Accept all clicks so they don't propagate to the menu and close it.
    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def _on_check(self, state: bool) -> None:
        self.included = state
        self.inclusionChanged.emit(state)

    def _on_name(self, text: str) -> None:
        self.vs_name = text
        self.nameChanged.emit(text)


class OutputDropdown(QPushButton):
    inclusionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items = list[OutputItemWidget]()
        self.total_outputs = 0
        self.shortest_dur_text = ""  # Set in init_load

        self.setMenu(NonClosingMenu(self))

        self.setStyleSheet("text-align: left; padding: 4px;")

    @property
    def included_outputs(self) -> list[int]:
        return [w.vs_index for w in self.items if w.included]

    def populate(self, voutputs: list[VideoOutputProxy]) -> None:
        self.menu().clear()
        self.items.clear()
        self.total_outputs = len(voutputs)

        for vout in voutputs:
            action = QWidgetAction(self.menu())
            action.setCheckable(True)

            widget = OutputItemWidget(vout, self.menu())
            action.setDefaultWidget(widget)
            self.menu().addAction(action)
            self.items.append(widget)

            widget.inclusionChanged.connect(self._update_text)
            widget.nameChanged.connect(self._update_text)

        self._update_text()

    def _update_text(self) -> None:
        included = [w for w in self.items if w.included]

        if len(included) == self.total_outputs and self.total_outputs > 0:
            self.setText(f"All outputs{self.shortest_dur_text}")
        elif len(included) == 0:
            self.setText(f"No output{self.shortest_dur_text}")
        else:
            names = [w.vs_name for w in included]
            self.setText(f"{', '.join(names)}{self.shortest_dur_text}")

        self.inclusionChanged.emit()


class FrameSourceProvider(StrEnum):
    MANUAL = "Manual"
    RANDOM = "Random"
    RANDOM_DARK = "Random dark"
    RANDOM_LIGHT = "Random light"


TIME_ROLE = Qt.ItemDataRole.UserRole


class ThumbnailItem(QListWidgetItem):
    @dataclass(slots=True)
    class Metadata:
        icon: QPixmap | QImage
        pict_type: str

    def __init__(
        self,
        time: Time,
        frame: int,
        src_provider: FrameSourceProvider,
        get_pict_type: bool = False,
    ) -> None:
        self.time = time
        self.frame = frame
        self.src_provider = src_provider
        self.get_pict_type = get_pict_type
        self.metadata = dict[int, ThumbnailItem.Metadata]()

        super().__init__(self._get_text("?"))

        self.setData(TIME_ROLE, time)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_tooltip("?")

    def update_metadata(self, idx: int) -> None:
        pict_type = "?"

        if idx in self.metadata:
            data = self.metadata[idx]
            pict_type = data.pict_type
            self.setText(self._get_text(pict_type))
            if not isinstance(data.icon, QPixmap):
                data.icon = QPixmap.fromImage(data.icon)
            self.setIcon(data.icon)

        self._update_tooltip(pict_type)

    def _update_tooltip(self, pict_type: str) -> None:
        base_text = f"{self.time.to_ts('{H:01d}:{M:02d}:{S:02d}.{ms:03d}')} ({self.frame})"
        if pict_type != "?":
            tooltip = f'{base_text} ({pict_type}) from "{self.src_provider}"'
        else:
            tooltip = f'{base_text} from "{self.src_provider}"'
        self.setToolTip(tooltip)

    def _get_text(self, pict_type: str) -> str:
        text = f"{self.time.to_ts('{H:01d}:{M:02d}:{S:02d}.{ms:03d}')} ({self.frame})"

        if pict_type != "?":
            text += f" ({pict_type})"

        return text


class FrameThumbnailList(QListWidget):
    ICON_SIZE = QSize(119, 67)

    listSizeChanged = Signal(int)  # delta

    def __init__(self, api: PluginAPI, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.included_outputs = list[VideoOutputProxy]()

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setIconSize(self.ICON_SIZE)
        self.setFixedHeight(125)
        self.setSpacing(4)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListWidget { padding-right: 12px; }")

        self.setMovement(QListWidget.Movement.Static)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.api.register_on_destroy(lambda: cachedproperty.clear_cache(self))
        self.api.register_on_destroy(lambda: setattr(self, "included_outputs", []))

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.remove_selected()
            event.accept()
        else:
            super().keyPressEvent(event)

    @cachedproperty
    def thumbnail_clips(self) -> dict[int, VideoNode]:
        out = dict[int, VideoNode]()

        for voutput in self.included_outputs:
            ar = voutput.vs_output.clip.width / voutput.vs_output.clip.height

            target_h = self.ICON_SIZE.height()
            target_w = round(target_h * ar)

            if target_w > self.ICON_SIZE.width():
                target_w = self.ICON_SIZE.width()
                target_h = round(target_w * 1 / ar)

            downscaled = core.resize.Bilinear(
                voutput.vs_output.clip,
                target_w,
                target_h,
                format=voutput.packer_sdr.format.vs,
            )
            packed = voutput.packer_sdr.to_rgb_packed(downscaled, alpha=None)

            out[voutput.vs_index] = packed

        return out

    def add_item(
        self,
        frame: int | None = None,
        get_pict_type: bool = False,
        src_provider: FrameSourceProvider = FrameSourceProvider.MANUAL,
    ) -> None:
        if frame is not None:
            time = self.api.current_voutput.frame_to_time(frame)
        else:
            time = self.api.current_time
            frame = self.api.current_frame

        # Reject duplicates
        for i in range(self.count()):
            if self.item(i).data(TIME_ROLE) == time:
                self.setCurrentRow(i)
                return

        item = ThumbnailItem(time, frame, src_provider, get_pict_type)
        # Ensure the item has space for the icon before it's loaded
        item.setSizeHint(QSize(self.ICON_SIZE.width() + self.spacing() * 2, 92))

        # Find sorted insertion position
        insert_idx = self.count()
        for i in range(insert_idx):
            if self.item(i).data(TIME_ROLE) > time:
                insert_idx = i
                break

        self.insertItem(insert_idx, item)
        self.scrollToItem(item)
        self.listSizeChanged.emit(1)

        self.fetch_thumbnail(item)

    def remove_selected(self) -> None:
        nb = len(self.selectedItems())

        for item in self.selectedItems():
            self.takeItem(self.row(item))

        self.listSizeChanged.emit(-nb)

    def get_data(self) -> list[tuple[Time, dict[int, ThumbnailItem.Metadata], FrameSourceProvider]]:
        return [
            (it.time, it.metadata, it.src_provider)
            for i in range(self.count())
            if isinstance(it := self.item(i), ThumbnailItem)
        ]

    @run_in_background(name="FetchThumbnail")
    def fetch_thumbnail(self, item: ThumbnailItem) -> None:
        def on_frame_rendered(f: Future[VideoFrame], idx: int) -> None:
            threading.current_thread().name = "FetchThumbnail"
            if f.exception():
                remove_item()

                logger.warning(
                    "Failed to fetch thumbnail for output %d at frame %d: %s",
                    idx,
                    item.frame,
                    f.exception(),
                )
                return

            with f.result() as frame:
                image = self.api.current_voutput.packer_sdr.frame_to_qimage(frame).copy()
                pict_type = (
                    get_prop(frame, "_PictType", str, default="?", func=self.fetch_thumbnail)
                    if item.get_pict_type
                    else "?"
                )
                item.metadata[idx] = ThumbnailItem.Metadata(image, pict_type)

                if idx == self.api.current_voutput.vs_index:
                    item.update_metadata(idx)

        @run_in_loop(return_future=False)
        def remove_item() -> None:
            if (row := self.row(item)) != -1:
                self.takeItem(row)

        with self.api.vs_context():
            for i, tclip in self.thumbnail_clips.items():
                frame_fut = tclip.get_frame_async(item.frame)
                frame_fut.add_done_callback(partial(on_frame_rendered, idx=i))

    @run_in_loop(return_future=False)
    def update_thumbnails(self, idx: int) -> None:
        for i in range(self.count()):
            if isinstance(it := self.item(i), ThumbnailItem):
                it.update_metadata(idx)

    def show_context_menu(self, pos: QPoint) -> None:
        if not self.itemAt(pos):
            return

        menu = QMenu(self)

        remove_action = menu.addAction(
            f"Remove {count} frame(s)" if (count := len(self.selectedItems())) > 1 else "Remove frame"
        )
        remove_action.triggered.connect(self.remove_selected)

        menu.exec(self.mapToGlobal(pos))
        menu.deleteLater()


class ProgressBar(QProgressBar):
    progressRunning = Signal()
    progressReady = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(24)
        self.setRange(0, 100)
        self.setTextVisible(True)
        self.setToolTip("Overall progress of extraction and upload tasks")

    @run_in_loop(return_future=False)
    def update_progress(
        self,
        *,
        value: int | None = None,
        range: tuple[int, int] | None = None,
        fmt: str | None = None,
        increment: int | None = None,
    ) -> None:
        self.progressRunning.emit()
        if range:
            self.setRange(*range)
        if fmt:
            self.setFormat(fmt)
        if value is not None:
            self.setValue(value)
        if increment is not None:
            self.setValue(self.value() + increment)

    @run_in_loop(return_future=False)
    def reset_progress(self) -> None:
        self.reset()
        self.setRange(0, 1)
        self.setFormat("%p%")
        self.progressReady.emit()


class PosterPayload(NamedTuple):
    result_serial: int
    row: int
    url: str


class AnchoredListPopup(QListWidget):
    def __init__(self, parent: QWidget, target: QWidget) -> None:
        super().__init__(parent)
        if not (app := QCoreApplication.instance()):
            raise SystemError

        self._target: QWidget | None = target
        self._target_window: QWidget | None = target.window()

        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMaximumHeight(300)
        self.hide()

        app.installEventFilter(self)
        self._target.installEventFilter(self)
        self._target_window.installEventFilter(self)

        self._target.destroyed.connect(self._on_target_destroyed)
        self.destroyed.connect(self._cleanup_event_filters)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            self._handle_app_click(event.globalPosition().toPoint())

        if watched in [self._safe_target(), self._target_window]:
            if event.type() in [QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show] and self.isVisible():
                self.update_geometry()
            elif event.type() in [QEvent.Type.Hide, QEvent.Type.WindowDeactivate]:
                self.hide()

        return super().eventFilter(watched, event)

    def update_geometry(self) -> None:
        if not (target := self._safe_target()):
            self.hide()
            return

        pos = target.mapToGlobal(QPoint(0, target.height()))
        self.setFixedWidth(target.width())
        self.move(pos)

    def _handle_app_click(self, global_pos: QPoint) -> None:
        if not self.isVisible():
            return

        clicked = QApplication.widgetAt(global_pos)
        target = self._safe_target()

        is_inside_popup = clicked is self or (clicked and self.isAncestorOf(clicked))
        is_inside_target = clicked and target and (clicked is target or target.isAncestorOf(clicked))

        if not (is_inside_popup or is_inside_target):
            self.hide()

    def _safe_target(self) -> QWidget | None:
        if self._target and Shiboken.isValid(self._target):
            return self._target

        self._target = None
        return None

    def _on_target_destroyed(self) -> None:
        self._target = None
        self._target_window = None
        self.hide()

    def _cleanup_event_filters(self, *_: Any) -> None:
        if (app := QCoreApplication.instance()) and Shiboken.isValid(app):
            app.removeEventFilter(self)

        if target := self._safe_target():
            target.removeEventFilter(self)

        if self._target_window and Shiboken.isValid(self._target_window):
            self._target_window.removeEventFilter(self)


class TMDBListPopup(AnchoredListPopup):
    POSTER_URL_PREFIX = "https://image.tmdb.org/t/p/w92"

    def __init__(self, parent: QWidget, target: QLineEdit) -> None:
        super().__init__(parent, target)
        self.setIconSize(QSize(32, 45))

        # Incremented on each new result set. Used to ignore stale async replies.
        self._results_serial = 0
        self._poster_cache = dict[str, QIcon]()
        self._poster_replies = dict[QNetworkReply, PosterPayload]()
        self._poster_loader = QNetworkAccessManager(self)
        self._poster_loader.finished.connect(self._on_poster_downloaded)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            delta = event.angleDelta().y() or event.angleDelta().x()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)

    @cachedproperty
    def tool_tip_template(self) -> jinja2.Template:
        return jinja2.Environment(
            loader=jinja2.FileSystemLoader(Path(__file__).parent),
            autoescape=jinja2.select_autoescape(),
        ).get_template("tmdb_tooltip.html.jinja")

    def show_results(self, results: list[TMDBTitle]) -> None:
        self._results_serial += 1
        # Cancel old requests when query/results change.
        self._cancel_poster_requests()
        self.clear()

        for row, value in enumerate(results):
            item = QListWidgetItem(value.name)
            item.setToolTip(self._get_tmdb_tooltip(value))
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.addItem(item)

            if not value.data.poster_path:
                continue

            url = f"{self.POSTER_URL_PREFIX}{value.data.poster_path}"
            if icon := self._poster_cache.get(url):
                item.setIcon(icon)
                continue

            # Load each poster asynchronously so text results appear instantly.
            reply = self._poster_loader.get(QNetworkRequest(url))
            self._poster_replies[reply] = PosterPayload(self._results_serial, row, url)

        self.setCurrentRow(0)
        self.update_geometry()
        self.show()

    def show_no_results(self) -> None:
        self._results_serial += 1
        self._cancel_poster_requests()
        self.clear()
        item = QListWidgetItem("No results found")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, None)
        self.addItem(item)
        self.clearSelection()
        self.setCurrentRow(-1)
        self.update_geometry()
        self.show()

    # Poster network
    def _cancel_poster_requests(self) -> None:
        if not self._poster_replies:
            return

        pending = list(self._poster_replies)
        self._poster_replies.clear()

        for reply in pending:
            reply.abort()
            reply.deleteLater()

    def _on_poster_downloaded(self, reply: QNetworkReply) -> None:
        if not (payload := self._poster_replies.pop(reply, None)):
            reply.deleteLater()
            return

        # Ignore responses from older result sets or failed downloads.
        if payload.result_serial != self._results_serial or reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return

        pixmap = QPixmap()
        pixmap.loadFromData(reply.readAll())

        if pixmap.isNull():
            reply.deleteLater()
            return

        icon = QIcon(
            pixmap.scaled(
                self.iconSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._poster_cache[payload.url] = icon

        if payload.row < self.count():
            item = self.item(payload.row)
            item.setIcon(icon)
            item.setToolTip(self._get_tmdb_tooltip(item.data(Qt.ItemDataRole.UserRole), pixmap_data_uri(pixmap)))

        reply.deleteLater()

    def _get_tmdb_tooltip(self, title: TMDBTitle, poster_data_uri: str | None = None) -> str:
        rows = [
            {"label": "Original title", "value": title.tooltip_data.original_name},
            {"label": "Genres", "value": title.tooltip_data.genres},
            {"label": "Language", "value": title.tooltip_data.language},
            {"label": "Country", "value": title.tooltip_data.country},
            {"label": "Release", "value": title.tooltip_data.release_date},
            {"label": "Rating", "value": title.tooltip_data.rating},
            {"label": "Popularity", "value": title.tooltip_data.popularity},
            {"label": "TMDB ID", "value": title.tooltip_data.tmdb_id},
        ]

        return self.tool_tip_template.render(
            header=title.tooltip_data.header,
            poster_data_uri=poster_data_uri,
            meta_rows=rows,
            overview=title.tooltip_data.overview,
        )

    @override
    def _cleanup_event_filters(self, *_: Any) -> None:
        self._cancel_poster_requests()
        super()._cleanup_event_filters()


@contextmanager
def open_qbuffer(mode: QIODevice.OpenModeFlag = QIODevice.OpenModeFlag.WriteOnly) -> Generator[QBuffer]:
    buffer = QBuffer()
    buffer.open(mode)
    try:
        yield buffer
    finally:
        buffer.close()


def pixmap_data_uri(pixmap: QPixmap) -> str:
    with open_qbuffer() as buffer:
        pixmap.save(buffer, "PNG", 100)

    b64_str = str(buffer.data().toBase64(), "ascii")  # type: ignore[call-overload]

    return f"data:image/png;base64,{b64_str}"


class FlowLayout(QLayout):
    """A layout that arranges widgets left-to-right, wrapping to new rows."""

    def __init__(self, parent: QWidget | None = None, margin: int = -1, h_spacing: int = 4, v_spacing: int = 4) -> None:
        super().__init__(parent)
        self._items = list[QLayoutItem]()
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)

    @override
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
        self.invalidate()

    @override
    def count(self) -> int:
        return len(self._items)

    @override
    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    @override
    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    @override
    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    @override
    def hasHeightForWidth(self) -> bool:
        return True

    @override
    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    @override
    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    @override
    def sizeHint(self) -> QSize:
        return self.minimumSize()

    @override
    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def insert_widget(self, index: int, widget: QWidget) -> None:
        self.addChildWidget(widget)
        item = QWidgetItem(widget)
        self._items.insert(index, item)
        self.invalidate()

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        row_h = 0

        for item in self._items:
            wid = item.widget()
            hint = item.sizeHint()
            w = hint.width()
            h = hint.height()

            next_x = x + w + self._h_spacing
            if next_x - self._h_spacing > effective.right() + 1 and row_h > 0:
                x = effective.x()
                y += row_h + self._v_spacing
                next_x = x + w + self._h_spacing
                row_h = 0

            if not test_only:
                # If this is the last item and it's a QLineEdit, stretch it to fill the row.
                if wid and item is self._items[-1] and isinstance(wid, QLineEdit):
                    remaining = effective.right() + 1 - x
                    w = max(w, remaining)
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))

            x = next_x
            row_h = max(row_h, h)

        return y + row_h - rect.y() + m.bottom()


class TagsLineEdit(QWidget):
    editingStarted = Signal()
    inputTextChanged = Signal(str)
    tagsChanged = Signal()

    STYLESHEET = """
        QWidget#tagsLineEdit {
            background: palette(button);
        }
        QWidget#tagsLineEdit QLineEdit#tagsInput {
            border: none;
            background: transparent;
            padding: 0px;
        }
        QWidget#tagsLineEdit QLineEdit#tagsInput:disabled {
            color: palette(mid);
        }
        QFrame#tagChip,
        QFrame#tagChip:hover,
        QFrame#tagChip:pressed {
            border: 1px solid palette(mid);
            border-radius: 10px;
            background: palette(button);
            color: palette(text);
        }
        QFrame#tagChip QLabel {
            background: transparent;
            border: none;
        }
        QToolButton#tagChipRemove,
        QToolButton#tagChipRemove:hover,
        QToolButton#tagChipRemove:pressed {
            border: none;
            background: transparent;
            padding: 0px;
            margin: 0px;
            min-width: 14px;
            max-width: 14px;
            min-height: 14px;
            max-height: 14px;
            color: palette(text);
        }
    """

    def __init__(self, parent: QWidget | None = None, *, placeholder_text: str | None = None) -> None:
        super().__init__(parent)
        self._chips = dict[str, QWidget]()
        self._tag_order = list[str]()
        self.setObjectName("tagsLineEdit")

        self._layout = FlowLayout(self, h_spacing=4, v_spacing=4)
        self._layout.setContentsMargins(6, 2, 6, 2)

        self._input = QLineEdit(self, placeholderText=placeholder_text)
        self._input.setObjectName("tagsInput")
        self._input.setFrame(False)
        self._input.setMinimumWidth(80)
        self._input.textChanged.connect(self.inputTextChanged.emit)
        self._input.installEventFilter(self)
        self._placeholder_text = placeholder_text or ""

        self._layout.addWidget(self._input)
        self.setFocusProxy(self._input)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(self.STYLESHEET)

        self.tagsChanged.connect(self._on_tags_changed)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

        with QPainter(self) as painter:
            option = QStyleOptionFrame()
            option.initFrom(self)
            option.rect = self.rect()
            option.lineWidth = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, option, self)
            option.midLineWidth = 0
            option.state |= QStyle.StateFlag.State_Sunken
            if self._input.hasFocus():
                option.state |= QStyle.StateFlag.State_HasFocus

            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelLineEdit, option, painter, self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._input:
            if event.type() == QEvent.Type.FocusIn:
                self.editingStarted.emit()
            elif event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    value = self.input_text().strip()
                    if value:
                        self.add_tag(value, value)
                        self.clear()
                        return True
                elif event.key() == Qt.Key.Key_Backspace and not self.input_text() and self._tag_order:
                    self.remove_tag(self._tag_order[-1])
                    return True

        return super().eventFilter(watched, event)

    def add_tag(self, value: str, label: str) -> bool:
        value = value.strip()
        label = label.strip() or value

        if not value or value in self._chips:
            return False

        chip = QFrame(self)
        chip.setObjectName("tagChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(6, 2, 4, 2)
        chip_layout.setSpacing(4)

        chip_label = QLabel(label, chip)
        chip_layout.addWidget(chip_label)

        remove_btn = QToolButton(chip)
        remove_btn.setObjectName("tagChipRemove")
        remove_btn.setText("x")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.remove_tag(value))
        chip_layout.addWidget(remove_btn)

        self._layout.insert_widget(self._layout.count() - 1, chip)

        self._chips[value] = chip
        self._tag_order.append(value)
        self.tagsChanged.emit()
        self.updateGeometry()
        return True

    def remove_tag(self, value: str) -> bool:
        if not (chip := self._chips.pop(value, None)):
            return False

        if value in self._tag_order:
            self._tag_order.remove(value)

        self._layout.removeWidget(chip)
        chip.deleteLater()
        self.tagsChanged.emit()
        self.updateGeometry()
        return True

    def selected_tags(self) -> list[str]:
        return self._tag_order.copy()

    def text(self) -> str:
        return ", ".join(self._tag_order)

    def input_text(self) -> str:
        return self._input.text()

    def clear(self) -> None:
        self._input.clear()

    def _on_tags_changed(self) -> None:
        if self._tag_order:
            self._input.setPlaceholderText("")
        else:
            self._input.setPlaceholderText(self._placeholder_text)


class TagsListPopup(AnchoredListPopup):
    tagPicked = Signal(str, str)  # value, label

    def __init__(self, parent: QWidget, target: QWidget) -> None:
        super().__init__(parent, target)
        self._all_tags = list[tuple[str, str]]()

        self.itemPressed.connect(self._on_item_selected)

    def set_tags(self, tags: Sequence[tuple[str, str]]) -> None:
        self._all_tags = list(tags)

    def has_tags(self) -> bool:
        return bool(self._all_tags)

    def show_filtered(self, query: str, selected: set[str]) -> None:
        if not self._all_tags:
            self.hide()
            return

        q = query.strip().lower()
        self.clear()

        for value, label in self._all_tags:
            if value in selected:
                continue

            if q and q not in label.lower() and q not in value.lower():
                continue

            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (value, label))
            self.addItem(item)

        if self.count() == 0:
            self.hide()
            return

        self.setCurrentRow(0)
        self.update_geometry()
        self.show()

    def _on_item_selected(self, item: QListWidgetItem) -> None:
        if not (data := item.data(Qt.ItemDataRole.UserRole)):
            return

        self.tagPicked.emit(*data)
        self.hide()


class PlaceholderLineEdit(QLineEdit):
    def __init__(self, items: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._completer = QCompleter(items, self)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._on_completer_activated)

    @override
    def keyPressEvent(self, e: QKeyEvent) -> None:
        if (
            (p := self._completer.popup())
            and p.isVisible()
            and e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab)
        ):
            return e.ignore()

        super().keyPressEvent(e)

        popup = self._completer.popup()
        i = self.text().rfind("{", 0, self.cursorPosition())
        if i != -1 and "}" not in self.text()[i : self.cursorPosition()] and popup:
            self._completer.setCompletionPrefix(self.text()[i + 1 : self.cursorPosition()])
            self._completer.complete()
        elif popup:
            popup.hide()
        return None

    def _on_completer_activated(self, s: str) -> None:
        t, p = self.text(), self.cursorPosition()
        i = t.rfind("{", 0, p)
        self.setText(f"{t[:i]}{{{s}}}{t[p:]}")
        self.setCursorPosition(i + len(s) + 2)


class LineEditCompleter(LineEdit):
    @override
    def create_widget(self, parent: QWidget | None = None) -> QLineEdit:
        return PlaceholderLineEdit([*TMDBTitle.format_hints, "vs_names"], parent)


class BrowserIDWidget(QWidget, IconReloadMixin):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.browser_id_lbl = QLabel(self)
        self.browser_id_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.browser_id_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.browser_id_lbl.setStyleSheet("""
            border: 1px solid palette(mid);
            border-radius: 4px;
            padding: 4px;
            background: palette(button);
        """)

        self.regenerate_id_btn = self.make_tool_button(IconName.ARROW_CLOCKWISE, "Generate a new browser ID", self)
        self.regenerate_id_btn.clicked.connect(self.on_regenerate_id_btn_clicked)

        self.copy_btn = self.make_tool_button(IconName.CLIPBOARD, "Copy browser ID", self)
        self.copy_btn.clicked.connect(self.on_copy_btn_clicked)

        layout.addWidget(self.browser_id_lbl)
        layout.addWidget(self.regenerate_id_btn)
        layout.addWidget(self.copy_btn)

    @property
    def id(self) -> str:
        return self.browser_id_lbl.text()

    @id.setter
    def id(self, value: str) -> None:
        self.browser_id_lbl.setText(value)

    def on_regenerate_id_btn_clicked(self) -> None:
        self.id = str(uuid4())

    def on_copy_btn_clicked(self) -> None:
        QApplication.clipboard().setText(self.browser_id_lbl.text())
        QToolTip.showText(QCursor.pos(), "Copied!", self.copy_btn)


class BrowserID(WidgetMetadata[BrowserIDWidget]):
    @override
    def create_widget(self, parent: QWidget | None = None) -> BrowserIDWidget:
        return BrowserIDWidget(parent)

    @override
    def load_value(self, widget: BrowserIDWidget, value: Any) -> None:
        with self.apply_transform(value, self.to_ui) as value:
            widget.id = value

    @override
    def get_value(self, widget: BrowserIDWidget) -> Any:
        with self.apply_transform(widget.id, self.from_ui) as value:
            return value
