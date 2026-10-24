from __future__ import annotations

from functools import partial
from logging import getLogger
from typing import override

import vapoursynth as vs
import vsengine.video
from jetpytools import fallback
from PySide6.QtCore import QRect, QSignalBlocker, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGraphicsView,
    QLabel,
    QMenu,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from vsview.api import (
    BaseGraphicsView,
    IconName,
    IconReloadMixin,
    NoCurrentVideoOutputError,
    PluginAPI,
    VideoOutputProxy,
    WidgetPluginBase,
    cache_clip,
    load_icon,
    run_in_loop,
)

from .constants import (
    LOADING_DEBOUNCE_MS,
    PLUGIN_DISPLAY_NAME,
    PLUGIN_IDENTIFIER,
    SPINNER_FRAMES,
    SPINNER_INTERVAL_MS,
    SPINNER_SIZE,
)
from .settings import GlobalSettings, LocalSettings

logger = getLogger(__name__)


class FrameSlotView(BaseGraphicsView):
    seekRequested = Signal(int)  # frame number

    def __init__(self, parent: QWidget | None = None, offset: int = 0) -> None:
        super().__init__(parent)
        self.offset = offset
        self.request_gen = 0
        self.target_frame: int | None = None
        self.pending_frame: int | None = None
        self.is_in_bounds = False
        self.is_loading = False
        self.show_loading_overlay = False
        self.spinner_pixmap = QPixmap()

        self.set_autofit(True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        self.context_menu.removeAction(self.slider_action)
        self.context_menu.removeAction(self.autofit_action)
        self.context_menu.removeAction(self.apply_sar_action)
        self.removeAction(self.autofit_action)

        self.update_cursor()

    # Properties
    @property
    def is_valid_frame(self) -> bool:
        return (
            self.is_in_bounds
            and not self.is_loading
            and self.target_frame is not None
            and self.target_frame >= 0
            and not self.pixmap_item.pixmap().isNull()
        )

    # Qt overrides
    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.is_valid_frame
            and self.offset != 0
            and self.target_frame is not None
        ):
            self.seekRequested.emit(self.target_frame)
            event.accept()
            return
        super().mousePressEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        viewport_rect = self.viewport().rect()

        # Draw dimmed viewport and centered spinner underneath the border/badge
        if self.is_loading and self.show_loading_overlay:
            dim_color = self.palette().color(QPalette.ColorRole.Window)
            dim_color.setAlpha(140)
            painter.fillRect(viewport_rect, dim_color)

            if not self.spinner_pixmap.isNull():
                dpr = self.spinner_pixmap.devicePixelRatio() or 1.0
                pw = round(self.spinner_pixmap.width() / dpr)
                ph = round(self.spinner_pixmap.height() / dpr)
                x = (viewport_rect.width() - pw) // 2
                y = (viewport_rect.height() - ph) // 2
                painter.drawPixmap(x, y, self.spinner_pixmap)

        # If current frame, draw an accent border
        if self.offset == 0:
            pen = QPen(self.palette().highlight().color(), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(viewport_rect.adjusted(1, 1, -1, -1))

        # Format badge text (reflect pending_frame immediately while loading)
        display_frame = self.pending_frame if self.pending_frame is not None else self.target_frame
        frame_str = str(display_frame) if self.is_in_bounds and display_frame is not None else "—"

        if self.offset == 0:
            label_text = f"0 ({frame_str})"
        elif self.offset > 0:
            label_text = f"+{self.offset} ({frame_str})"
        else:
            label_text = f"{self.offset} ({frame_str})"

        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)

        fm = painter.fontMetrics()
        text_width = fm.horizontalAdvance(label_text)
        text_height = fm.height()

        padding_h = 6
        padding_v = 2
        badge_w = text_width + (padding_h * 2)
        badge_h = text_height + (padding_v * 2)

        badge_rect = QRect(6, 6, badge_w, badge_h)

        # Draw semi-transparent background pill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
        painter.drawRoundedRect(badge_rect, 4, 4)

        # Draw pill text
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, label_text)

        painter.end()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.set_zoom(0)

    # Prevent thumbnail slots from registering global view shortcuts
    @override
    def _setup_shortcuts(self) -> None: ...

    # Helpers
    def is_up_to_date(self, target_frame: int, in_bounds: bool) -> bool:
        if self.is_loading:
            return self.pending_frame == target_frame
        if in_bounds:
            return self.is_valid_frame and self.target_frame == target_frame
        return not self.is_in_bounds and self.target_frame == target_frame

    def update_cursor(self) -> None:
        if self.is_valid_frame and self.offset != 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_loading(self, target_frame: int) -> None:
        self.is_in_bounds = True
        self.is_loading = True
        self.show_loading_overlay = False
        self.pending_frame = target_frame
        self.update_cursor()
        self.viewport().update()

    def set_frame_image(self, image: QImage, target_frame: int) -> None:
        self.is_in_bounds = True
        self.is_loading = False
        self.show_loading_overlay = False
        self.pending_frame = None
        self.target_frame = target_frame
        self.set_pixmap(QPixmap.fromImage(image))
        self.set_zoom(0)
        self.update_cursor()
        self.viewport().update()

    def set_frame_error(self, target_frame: int) -> None:
        self.is_in_bounds = True
        self.is_loading = False
        self.show_loading_overlay = False
        self.pending_frame = None
        self.target_frame = target_frame
        self.set_pixmap(QPixmap())
        self.update_cursor()
        self.viewport().update()

    def set_out_of_bounds(self, target_frame: int | None = None) -> None:
        self.is_in_bounds = False
        self.is_loading = False
        self.show_loading_overlay = False
        self.pending_frame = None
        self.target_frame = target_frame
        self.set_pixmap(QPixmap())
        self.update_cursor()
        self.viewport().update()


class FrameStripPlugin(WidgetPluginBase[GlobalSettings, LocalSettings], IconReloadMixin):
    identifier = PLUGIN_IDENTIFIER
    display_name = PLUGIN_DISPLAY_NAME

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        IconReloadMixin.__init__(self)

        self.outputs = dict[VideoOutputProxy, vs.VideoNode]()
        self.slots = dict[int, FrameSlotView]()
        self.slot_pool = dict[int, FrameSlotView]()
        self.active_voutput: VideoOutputProxy | None = None
        self.spinner_frame_index = 0
        self.spinner_frames = list[QPixmap]()
        self.neighbor_count_cached = self.neighbor_count

        self._debounce_timer = QTimer(self, singleShot=True, interval=LOADING_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._on_loading_debounce_timeout)

        self._spinner_timer = QTimer(self, interval=SPINNER_INTERVAL_MS)
        self._spinner_timer.timeout.connect(self._animate_spinner)

        self._reload_icons()
        self.register_icon_callback(self._reload_icons)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.strip_container = QWidget(self)
        self.strip_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.strip_container)
        self.strip_layout.setContentsMargins(4, 4, 4, 4)
        self.strip_layout.setSpacing(6)

        self.separator = QFrame(self.strip_container, frameShape=QFrame.Shape.VLine, frameShadow=QFrame.Shadow.Sunken)

        self.main_layout.addWidget(self.strip_container)

        self._rebuild_slots()

        self.api.globalSettingsChanged.connect(self._on_settings_changed)
        self.api.localSettingsChanged.connect(self._on_settings_changed)
        self.api.register_on_destroy(self._on_destroy)

    # Properties
    @property
    def neighbor_count(self) -> int:
        return fallback(self.settings.local_.neighbor_count, self.settings.global_.neighbor_count)

    @property
    def step(self) -> int:
        return fallback(self.settings.local_.step, self.settings.global_.step)

    @property
    def show_current_frame(self) -> bool:
        return fallback(self.settings.local_.show_current_frame, self.settings.global_.show_current_frame)

    # Qt overrides
    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_layout_direction()

    # Plugin overrides
    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        super().on_current_voutput_changed(self._cache_voutput(voutput), tab_index)

    @override
    def on_current_frame_changed(self, n: int) -> None:
        self._fetch_neighbor_frames(n)

    @override
    def on_playback_stopped(self) -> None:
        self._fetch_neighbor_frames(skip_valid=True)

    @override
    def on_hide(self) -> None:
        self._stop_loading_timers()

    # Helpers
    def _update_layout_direction(self) -> None:
        if self.width() >= self.height():
            target_direction = QBoxLayout.Direction.LeftToRight
            target_shape = QFrame.Shape.VLine
        else:
            target_direction = QBoxLayout.Direction.TopToBottom
            target_shape = QFrame.Shape.HLine

        if self.strip_layout.direction() != target_direction:
            self.strip_layout.setDirection(target_direction)
            self.separator.setFrameShape(target_shape)

    def _rebuild_slots(self) -> None:
        count = self.neighbor_count
        prev_offsets = range(-count, 0)
        next_offsets = range(1, count + 1)
        target_offsets = [*prev_offsets, *([0] if self.show_current_frame else []), *next_offsets]

        if list(self.slots.keys()) == target_offsets:
            self._update_layout_direction()
            return

        self.strip_container.setUpdatesEnabled(False)
        try:
            while self.strip_layout.count():
                self.strip_layout.takeAt(0)

            target_set = set(target_offsets)
            for offset, slot in self.slot_pool.items():
                if offset not in target_set:
                    slot.hide()

            self.slots.clear()

            # Add previous slots
            for offset in prev_offsets:
                slot = self._get_slot(offset)
                self.slots[offset] = slot
                self.strip_layout.addWidget(slot, 1)
                slot.show()

            # Center: either current frame slot or separator
            if self.show_current_frame:
                self.separator.hide()
                slot = self._get_slot(0)
                self.slots[0] = slot
                self.strip_layout.addWidget(slot, 1)
                slot.show()
            else:
                self.strip_layout.addWidget(self.separator)
                self.separator.show()

            # Add next slots
            for offset in next_offsets:
                slot = self._get_slot(offset)
                self.slots[offset] = slot
                self.strip_layout.addWidget(slot, 1)
                slot.show()

            self._update_layout_direction()
        finally:
            self.strip_container.setUpdatesEnabled(True)

        self._check_loading_finished()

    def _get_slot(self, offset: int) -> FrameSlotView:
        if (slot := self.slot_pool.get(offset)) is None:
            slot = self._create_slot(offset)
            self.slot_pool[offset] = slot
        return slot

    def _create_slot(self, offset: int) -> FrameSlotView:
        slot = FrameSlotView(self.strip_container, offset)
        slot.spinner_pixmap = self.spinner_frames[0]
        slot.seekRequested.connect(self._on_requested_seek)
        # Append inline settings to the context menu
        slot.context_menu.addSeparator()
        slot.context_menu.addAction(self._create_settings_action(slot.context_menu))
        return slot

    def _create_settings_action(self, menu: QMenu) -> QWidgetAction:
        action = QWidgetAction(menu)
        container = QWidget(menu)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        title = QLabel("Frame Strip Settings", container)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)

        count_spin = QSpinBox(container)
        count_spin.setRange(1, 5)
        count_spin.setValue(self.neighbor_count)

        step_spin = QSpinBox(container)
        step_spin.setRange(1, 1000)
        step_spin.setValue(self.step)

        current_check = QCheckBox("Show current frame", container)
        current_check.setChecked(self.show_current_frame)

        form.addRow("Neighbors:", count_spin)
        form.addRow("Step:", step_spin)
        form.addRow("", current_check)
        layout.addLayout(form)

        def sync_values() -> None:
            with QSignalBlocker(count_spin), QSignalBlocker(step_spin), QSignalBlocker(current_check):
                count_spin.setValue(self.neighbor_count)
                step_spin.setValue(self.step)
                current_check.setChecked(self.show_current_frame)

        menu.aboutToShow.connect(sync_values)
        count_spin.valueChanged.connect(self._on_count_changed)
        step_spin.valueChanged.connect(self._on_step_changed)
        current_check.toggled.connect(self._on_current_toggled)

        action.setDefaultWidget(container)
        return action

    def _cache_voutput(self, voutput: VideoOutputProxy) -> VideoOutputProxy:
        if self.neighbor_count != self.neighbor_count_cached:
            self.outputs.clear()
            self.neighbor_count_cached = self.neighbor_count

        if voutput not in self.outputs:
            with self.api.vs_context():
                packed = voutput.packer_sdr.pack_clip(voutput.vs_output.clip, voutput.vs_output.alpha)
                self.outputs[voutput] = cache_clip(packed, self.neighbor_count * 2 + 1)
        return voutput

    @run_in_loop
    def _fetch_neighbor_frames(self, n: int | None = None, *, skip_valid: bool = False) -> None:
        if not self.isVisible() or self.api.is_playing:
            return

        try:
            voutput = self._cache_voutput(self.api.current_voutput)
        except NoCurrentVideoOutputError:
            return

        if self.active_voutput is not voutput:
            self.active_voutput = voutput
            for slot in self.slot_pool.values():
                slot.request_gen += 1
                slot.is_loading = False
                slot.pending_frame = None
                slot.target_frame = None

        packed_clip = self.outputs[voutput]
        current_frame = fallback(n, self.api.current_frame)
        num_frames = voutput.vs_output.clip.num_frames

        def on_frame_success(
            frame: vs.VideoFrame,
            slot: FrameSlotView,
            frame_num: int,
            request_gen: int,
            vo: VideoOutputProxy,
        ) -> None:
            with frame:
                qimage = vo.packer_sdr.frame_to_qimage(frame).owned(frame.get_read_ptr(0))
            self._on_frame_rendered(slot, frame_num, request_gen, vo, qimage)

        def on_frame_error(
            e: BaseException,
            slot: FrameSlotView,
            frame_num: int,
            request_gen: int,
            vo: VideoOutputProxy,
        ) -> None:
            logger.error("Failed to render frame %d for offset %d", frame_num, slot.offset, exc_info=e)
            self._on_frame_rendered(slot, frame_num, request_gen, vo, None)

        for offset, slot in list(self.slots.items()):
            target_frame = current_frame + offset * self.step
            in_bounds = 0 <= target_frame < num_frames

            if skip_valid and slot.is_up_to_date(target_frame, in_bounds):
                continue

            slot.request_gen += 1
            gen = slot.request_gen

            if in_bounds:
                slot.set_loading(target_frame)
                s = partial(on_frame_success, slot=slot, frame_num=target_frame, request_gen=gen, vo=voutput)
                e = partial(on_frame_error, slot=slot, frame_num=target_frame, request_gen=gen, vo=voutput)
                with self.api.vs_context():
                    vsengine.video.frame(packed_clip, target_frame).then(s, e).catch(e)
            else:
                slot.set_out_of_bounds(target_frame)

        self._start_loading_debounce()

    @run_in_loop
    def _on_frame_rendered(
        self,
        slot: FrameSlotView,
        frame_num: int,
        request_gen: int,
        vo: VideoOutputProxy,
        image: QImage | None,
    ) -> None:
        if slot.request_gen != request_gen or self.api.current_voutput != vo:
            return
        slot.set_frame_image(image, frame_num) if image is not None else slot.set_frame_error(frame_num)
        self._check_loading_finished()

    def _start_loading_debounce(self) -> None:
        self._spinner_timer.stop()
        if any(slot.is_loading for slot in self.slots.values()):
            self._debounce_timer.start()
        else:
            self._debounce_timer.stop()

    def _check_loading_finished(self) -> None:
        if not any(slot.is_loading for slot in self.slots.values()):
            self._stop_loading_timers()

    def _stop_loading_timers(self) -> None:
        self._debounce_timer.stop()
        self._spinner_timer.stop()

    def _on_destroy(self) -> None:
        self._stop_loading_timers()
        self.outputs.clear()
        self.active_voutput = None
        for slot in self.slot_pool.values():
            slot.request_gen += 1

    def _reload_icons(self) -> None:
        color = self.palette().color(QPalette.ColorRole.WindowText)
        base_spinner = load_icon(IconName.SPINNER_GAP, SPINNER_SIZE, color)
        self.spinner_frames = self._generate_spinner_frames(base_spinner)

        current_pixmap = self.spinner_frames[self.spinner_frame_index % len(self.spinner_frames)]
        for slot in self.slot_pool.values():
            slot.spinner_pixmap = current_pixmap
            if slot.is_loading and slot.show_loading_overlay:
                slot.viewport().update()

    def _generate_spinner_frames(self, base_pixmap: QPixmap) -> list[QPixmap]:
        frames = list[QPixmap]()
        angle_step = 360 / SPINNER_FRAMES

        for i in range(SPINNER_FRAMES):
            transform = QTransform().rotate(i * angle_step)
            rotated = base_pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)

            x = (rotated.width() - base_pixmap.width()) // 2
            y = (rotated.height() - base_pixmap.height()) // 2

            frame = rotated.copy(x, y, base_pixmap.width(), base_pixmap.height())
            frame.setDevicePixelRatio(base_pixmap.devicePixelRatio())
            frames.append(frame)

        return frames

    # Slots
    @Slot()
    def _on_loading_debounce_timeout(self) -> None:
        if not (loading_slots := [slot for slot in self.slots.values() if slot.is_loading]):
            return

        self.spinner_frame_index = 0
        initial_pixmap = self.spinner_frames[0]

        for slot in loading_slots:
            slot.show_loading_overlay = True
            slot.spinner_pixmap = initial_pixmap
            slot.viewport().update()

        self._spinner_timer.start()

    @Slot()
    def _animate_spinner(self) -> None:
        if not (loading_slots := [s for s in self.slots.values() if s.is_loading and s.show_loading_overlay]):
            self._spinner_timer.stop()
            return

        self.spinner_frame_index = (self.spinner_frame_index + 1) % SPINNER_FRAMES
        pixmap = self.spinner_frames[self.spinner_frame_index]

        for slot in loading_slots:
            slot.spinner_pixmap = pixmap
            slot.viewport().update()

    @Slot(int)
    def _on_requested_seek(self, n: int) -> None:
        self.api.playback.seek(n)

    @Slot()
    def _on_settings_changed(self) -> None:
        if self.neighbor_count != self.neighbor_count_cached:
            self.outputs.clear()
            self.neighbor_count_cached = self.neighbor_count
        self._rebuild_slots()
        self._fetch_neighbor_frames(skip_valid=True)

    @Slot(int)
    def _on_count_changed(self, val: int) -> None:
        self.update_local_settings(neighbor_count=val)
        if val != self.neighbor_count_cached:
            self.outputs.clear()
            self.neighbor_count_cached = val
        self._rebuild_slots()
        self._fetch_neighbor_frames(skip_valid=True)

    @Slot(int)
    def _on_step_changed(self, val: int) -> None:
        self.update_local_settings(step=val)
        self._fetch_neighbor_frames(skip_valid=True)

    @Slot(bool)
    def _on_current_toggled(self, checked: bool) -> None:
        self.update_local_settings(show_current_frame=checked)
        self._rebuild_slots()
        self._fetch_neighbor_frames(skip_valid=True)
