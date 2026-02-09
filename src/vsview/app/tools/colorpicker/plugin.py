from __future__ import annotations

import ctypes
from colorsys import rgb_to_hls, rgb_to_hsv
from enum import Enum, auto
from functools import partial
from math import ceil, floor, log, log10
from struct import unpack
from typing import Annotated, Any

import vapoursynth as vs
from jetpytools import clamp
from pydantic import BaseModel
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QContextMenuEvent, QCursor, QImage, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from vsview.api import IconName, IconReloadMixin, PluginAPI, Spin, VideoOutputProxy, WidgetPluginBase, run_in_loop
from vsview.app.outputs.packing import Packer
from vsview.app.utils import cache_clip
from vsview.assets.utils import get_monospace_font

from .utils import get_chroma_offsets, scale_value_to_float

type CachedVideoNode = vs.VideoNode


# ctypes data types for pixel extraction
DATA_TYPES: dict[vs.SampleType, dict[int, type[ctypes._SimpleCData[Any]]]] = {
    vs.INTEGER: {
        1: ctypes.c_uint8,
        2: ctypes.c_uint16,
        4: ctypes.c_uint32,
    },
    vs.FLOAT: {
        2: ctypes.c_char,  # half-float handled specially via struct.unpack
        4: ctypes.c_float,
    },
}


class TrackingState(Enum):
    INACTIVE = auto()
    """Eyedropper mode off"""

    ACTIVE = auto()
    """Tracking mouse movement"""

    DEACTIVATING = auto()
    """Right-clicked, waiting for context menu block"""


class PositionLabel(QLabel):
    def __init__(self, parent: QWidget, font_size: int = 12) -> None:
        super().__init__("Pos: —, —", parent)
        self.setFont(get_monospace_font(font_size))
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._cursor_pos = QPoint(0, 0)

    @property
    def cursor_pos(self) -> QPoint:
        return self._cursor_pos

    @cursor_pos.setter
    def cursor_pos(self, value: QPoint) -> None:
        self._cursor_pos = value
        self.setText(f"Pos: {self._cursor_pos.x()}, {self._cursor_pos.y()}")


class GlobalSettings(BaseModel):
    decimals_nb: Annotated[
        int,
        Spin(
            label="Decimals",
            min=0,
            max=10,
            tooltip="Number of decimals to display for float values",
        ),
    ] = 5


class ColorPickerPlugin(WidgetPluginBase[GlobalSettings], IconReloadMixin):
    identifier = "jet_vsview_colorpicker"
    display_name = "Color Picker"

    # Grid column indices
    COL_LABEL = 0
    COL_VALUES_START = 1

    RGB30_FORMATS: tuple[QImage.Format, ...] = Packer.FORMAT_CONFIG[10][2:]
    RGBA_FORMATS: tuple[QImage.Format, ...] = tuple(zip(*Packer.FORMAT_CONFIG.values()))[3]

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        IconReloadMixin.__init__(self)

        self.tracking = TrackingState.INACTIVE
        self.outputs = dict[VideoOutputProxy, CachedVideoNode]()

        self.current_num_planes = 0
        self.current_rgb_cols = 3  # Track RGB columns (3 for RGB, 4 for RGBA)

        # Format strings for source values
        self.src_hex_fmt = ""
        self.src_dec_fmt = ""
        self.src_norm_fmt = f"{{:.{self.settings.global_.decimals_nb}f}}"

        # Value label storage: {row_name: [labels]}
        self.src_labels = dict[str, list[QLabel]]()
        self.rgb_labels = dict[str, list[QLabel]]()
        self.src_copy_btns = dict[str, QToolButton]()
        self.rgb_copy_btns = dict[str, QToolButton]()

        self.setup_ui()
        self.api.register_on_destroy(self.outputs.clear)
        self.api.register_on_destroy(lambda: self.eyedropper_btn.setChecked(False))

    def setup_ui(self) -> None:
        self.main_container = QWidget(self)
        self.main_container.setMaximumWidth(1000)
        self.main_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.outer_layout = QHBoxLayout(self)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_layout.setSpacing(0)
        self.outer_layout.addStretch(1)
        self.outer_layout.addWidget(self.main_container, alignment=Qt.AlignmentFlag.AlignLeft)
        self.outer_layout.addStretch(1)

        self.main_layout = QVBoxLayout(self.main_container)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(8)

        # Top bar: toggle & position
        top_bar_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)

        self.position_label = PositionLabel(self)
        top_bar_layout.addWidget(self.position_label)

        top_bar_layout.addStretch()

        self.color_preview = QLabel(self)
        self.color_preview.setFixedSize(32, 24)
        self.color_preview.setStyleSheet("background-color: transparent; border-radius: 4px;")
        top_bar_layout.addWidget(self.color_preview)

        self.eyedropper_btn = self.make_tool_button(
            IconName.PIPETTE,
            "Pick color from image",
            self,
            checkable=True,
            icon_size=QSize(24, 24),
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.eyedropper_btn.toggled.connect(self.on_eyedropper_toggle)
        top_bar_layout.addWidget(self.eyedropper_btn)

        self.main_layout.addLayout(top_bar_layout)

        # Container for the two groups (responsive layout)
        self.groups_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.groups_layout.setSpacing(8)
        self.main_layout.addLayout(self.groups_layout)

        # Source section
        self.src_group = QGroupBox("Source", self)
        self.src_group.setStyleSheet("font: 10pt;")
        self.src_grid = QGridLayout(self.src_group)
        self.src_grid.setContentsMargins(4, 4, 4, 4)
        self.src_grid.setSpacing(2)
        self.groups_layout.addWidget(self.src_group)

        # Rendered section
        self.rgb_group = QGroupBox("Rendered (RGB)", self)
        self.rgb_group.setStyleSheet("font: 10pt;")
        self.rgb_grid = QGridLayout(self.rgb_group)
        self.rgb_grid.setContentsMargins(4, 4, 4, 4)
        self.rgb_grid.setSpacing(2)
        self.groups_layout.addWidget(self.rgb_group)

        # Initialize grids
        self.setup_grid_rows(
            self.src_grid,
            self.src_labels,
            self.src_copy_btns,
            ["Dec", "Norm", "Hex"],
            3,
        )
        self.setup_grid_rows(
            self.rgb_grid,
            self.rgb_labels,
            self.rgb_copy_btns,
            ["Dec", "Norm", "Hex", "HLS", "HSV"],
            self.current_rgb_cols,
        )

        self.main_layout.addStretch()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)

        if self.height() < 350 and self.width() > 300:
            self.groups_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        else:
            self.groups_layout.setDirection(QBoxLayout.Direction.TopToBottom)

    # Plugin hooks
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        if voutput not in self.outputs:
            self.outputs[voutput] = cache_clip(voutput.vs_output.clip, 10)

        super().on_current_voutput_changed(voutput, tab_index)

    def on_current_frame_changed(self, n: int) -> None:
        if self.api.is_playing:
            return

        # Add the voutput to the `outputs` dict and call `on_current_frame_changed` again
        if (voutput := self.api.current_voutput) not in self.outputs:
            return self.on_current_voutput_changed(voutput, self.api.current_video_index)

        if self.tracking == TrackingState.ACTIVE:
            # Force cursor shape & refresh current cursor position
            self.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)
            self.update_labels(self.api.current_view.viewport.map_from_global(QCursor.pos()))

    def on_view_context_menu(self, event: QContextMenuEvent) -> None:
        if self.tracking == TrackingState.DEACTIVATING or self.eyedropper_btn.isChecked():
            self.eyedropper_btn.setChecked(False)
            event.ignore()

    def on_view_mouse_moved(self, event: QMouseEvent) -> None:
        if self.tracking == TrackingState.ACTIVE and not self.api.is_playing:
            self.update_labels(event.position().toPoint())

    def on_view_mouse_pressed(self, event: QMouseEvent) -> None:
        if self.tracking == TrackingState.ACTIVE and event.button() == Qt.MouseButton.RightButton:
            self.tracking = TrackingState.DEACTIVATING
            self.api.current_view.viewport.set_cursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def on_view_mouse_released(self, event: QMouseEvent) -> None:
        if self.tracking == TrackingState.ACTIVE and event.button() == Qt.MouseButton.LeftButton:
            self.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)

    # Plugin methods
    def setup_grid_rows(
        self,
        grid: QGridLayout,
        labels_dict: dict[str, list[QLabel]],
        copy_btns_dict: dict[str, QToolButton],
        row_names: list[str],
        num_cols: int,
    ) -> None:
        # Clear existing widgets
        while grid.count():
            if (item := grid.takeAt(0)) and (widget := item.widget()):
                widget.deleteLater()

        labels_dict.clear()
        copy_btns_dict.clear()

        for row_idx, name in enumerate(row_names):
            # Row label
            lbl = QLabel(f"{name}:", self)
            lbl.setFixedWidth(45)
            lbl.setStyleSheet("font: 10pt;")
            grid.addWidget(lbl, row_idx, self.COL_LABEL)

            # Value labels
            val_labels = list[QLabel]()

            for col in range(num_cols):
                val_lbl = QLabel(
                    "—",
                    self,
                    textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse,
                    alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                )
                val_lbl.setFont(get_monospace_font(10))
                val_lbl.setMinimumWidth(65)
                val_lbl.setCursor(Qt.CursorShape.IBeamCursor)
                val_labels.append(val_lbl)
                grid.addWidget(val_lbl, row_idx, self.COL_VALUES_START + col)

            labels_dict[name] = val_labels

            # Copy button
            copy_btn = self.make_tool_button(IconName.CLIPBOARD, f"Copy {name} values", self)
            copy_btn.setFixedSize(24, 24)
            copy_btn.clicked.connect(partial(self.copy_row, name, labels_dict, copy_btns_dict))
            grid.addWidget(copy_btn, row_idx, self.COL_VALUES_START + num_cols)
            copy_btns_dict[name] = copy_btn

    @run_in_loop
    def update_labels(self, local_pos: QPoint) -> None:
        with self.outputs[self.api.current_voutput].get_frame(self.api.current_frame) as vsframe:
            self.update_format_strings(vsframe)

        pos = self.api.current_view.map_to_scene(local_pos).toPoint()

        if (image := self.api.current_view.image).isNull() or not image.valid(pos):
            return

        self._update_source_labels(pos)
        self._update_rgb_labels(pos, image)

    @run_in_loop(return_future=False)
    def update_format_strings(self, frame: vs.VideoFrame) -> None:
        # Read format on VideoFrame to gracefully support variable format clips
        self.src_group.setTitle(f"Source ({frame.format.name})")

        # Update source grid
        if (num_planes := frame.format.num_planes) != self.current_num_planes:
            self.current_num_planes = num_planes
            self.setup_grid_rows(
                self.src_grid,
                self.src_labels,
                self.src_copy_btns,
                ["Dec", "Norm", "Hex"],
                num_planes,
            )

        if frame.format.sample_type == vs.INTEGER:
            src_max_val = 2**frame.format.bits_per_sample - 1

            hex_width = ceil(log(max(src_max_val, 2), 16))
            dec_width = ceil(log10(max(src_max_val, 2)))

            self.src_hex_fmt = f"{{:<{hex_width}X}}"
            self.src_dec_fmt = f"{{:<{dec_width}d}}"
        else:
            self.src_hex_fmt = ""  # No hex for float
            self.src_dec_fmt = f"{{:.{self.settings.global_.decimals_nb}f}}"

        self.src_norm_fmt = f"{{:.{self.settings.global_.decimals_nb}f}}"

    # Internal methods
    def _update_source_labels(self, pos: QPoint) -> None:
        with self.api.vs_context(), self.outputs[self.api.current_voutput].get_frame(self.api.current_frame) as frame:
            src_vals = self._extract_pixel_values(frame, pos)

            self._set_row_values(self.src_labels, "Dec", [self.src_dec_fmt.format(v) for v in src_vals])

            if frame.format.sample_type == vs.INTEGER:
                self._set_row_values(self.src_labels, "Hex", [self.src_hex_fmt.format(v) for v in src_vals])

                norm_vals = [
                    clamp(scale_value_to_float(v, frame, (chroma := not not i)) + 0.5 * chroma, 0, 1.0)  # noqa: SIM208
                    for i, v in enumerate(src_vals)
                ]
            else:
                self._set_row_values(self.src_labels, "Hex", ["—"] * len(src_vals))

                norm_vals = list[float]()
                for i, v in enumerate(src_vals):
                    if i == 0 or frame.format.color_family == vs.RGB:
                        norm_vals.append(clamp(v, 0, 1))
                    else:
                        norm_vals.append(clamp(v, -0.5, 0.5) + 0.5)

            self._set_row_values(self.src_labels, "Norm", [self.src_norm_fmt.format(v) for v in norm_vals])

    def _update_rgb_labels(self, pos: QPoint, image: QImage) -> None:
        img_format = image.format()
        color = image.pixelColor(pos)

        is_rgb30 = img_format in self.RGB30_FORMATS
        has_alpha = img_format in self.RGBA_FORMATS
        max_val = 1023 if is_rgb30 else 255

        # Rebuild grid if alpha state changed
        required_cols = 4 if has_alpha else 3
        if required_cols != self.current_rgb_cols:
            self.current_rgb_cols = required_cols
            self.setup_grid_rows(
                self.rgb_grid,
                self.rgb_labels,
                self.rgb_copy_btns,
                ["Dec", "Norm", "Hex", "HLS", "HSV"],
                self.current_rgb_cols,
            )

        self.rgb_group.setTitle(f"Rendered ({self.api.packer.vs_format.name}{'A' if has_alpha else ''})")

        r_f, g_f, b_f, a_f = color.redF(), color.greenF(), color.blueF(), color.alphaF()
        r, g, b, a = round(r_f * max_val), round(g_f * max_val), round(b_f * max_val), round(a_f * max_val)

        self.position_label.cursor_pos = pos

        rgb_norm = f"{{:.{self.settings.global_.decimals_nb}f}}"
        hex_fmt = f"{{:0{3 if is_rgb30 else 2}X}}"

        # Build value lists based on alpha presence
        hex_vals = [hex_fmt.format(r), hex_fmt.format(g), hex_fmt.format(b)]
        dec_vals = [f"{r}", f"{g}", f"{b}"]
        norm_vals = [rgb_norm.format(r_f), rgb_norm.format(g_f), rgb_norm.format(b_f)]

        if has_alpha:
            hex_vals.append(hex_fmt.format(a))
            dec_vals.append(f"{a}")
            norm_vals.append(rgb_norm.format(a_f))

        self._set_row_values(self.rgb_labels, "Hex", hex_vals)
        self._set_row_values(self.rgb_labels, "Dec", dec_vals)
        self._set_row_values(self.rgb_labels, "Norm", norm_vals)

        hls = rgb_to_hls(r_f, g_f, b_f)
        hsv = rgb_to_hsv(r_f, g_f, b_f)

        # HLS/HSV always 3 values. Pad with "—" for alpha column.
        hls_vals = [f"{int(hls[0] * 360)}°", f"{int(hls[1] * 100)}%", f"{int(hls[2] * 100)}%"]
        hsv_vals = [f"{int(hsv[0] * 360)}°", f"{int(hsv[1] * 100)}%", f"{int(hsv[2] * 100)}%"]

        if has_alpha:
            hls_vals.append("—")
            hsv_vals.append("—")

        self._set_row_values(self.rgb_labels, "HLS", hls_vals)
        self._set_row_values(self.rgb_labels, "HSV", hsv_vals)

        preview_r, preview_g, preview_b = round(r_f * 255), round(g_f * 255), round(b_f * 255)
        self.color_preview.setStyleSheet(
            f"background-color: rgb({preview_r},{preview_g},{preview_b}); border: 1px solid gray; border-radius: 4px;"
        )

    def _set_row_values(self, labels_dict: dict[str, list[QLabel]], row_name: str, values: list[str]) -> None:
        for i, val in enumerate(values):
            labels_dict[row_name][i].setText(val)

    def _extract_pixel_values(self, frame: vs.VideoFrame, pos: QPoint) -> list[float]:
        results = list[float]()

        fmt = frame.format
        bps = fmt.bytes_per_sample
        data_type = DATA_TYPES[fmt.sample_type][bps]

        for plane in range(fmt.num_planes):
            stride = frame.get_stride(plane)
            ptr = frame.get_read_ptr(plane)

            plane_h, plane_w = frame[plane].shape
            buffer_size = (stride * plane_h) // bps
            buffer = (data_type * buffer_size).from_address(ptr.value or 0)

            # Get offsets to match the grid alignment
            off_y, off_x = get_chroma_offsets(frame) if plane else (0.0, 0.0)

            y = clamp(floor((pos.y() * plane_h / frame.height) + off_y), 0, plane_h - 1)
            x = clamp(floor((pos.x() * plane_w / frame.width) + off_x), 0, plane_w - 1)

            # fp16
            if fmt.sample_type == vs.FLOAT and bps == 2:
                byte_offset = y * stride + x * 2
                res = unpack("e", buffer[byte_offset : byte_offset + 2])[0]  # type: ignore[arg-type]
            else:
                res = buffer[y * (stride // bps) + x]

            results.append(res)  # type: ignore[arg-type]

        return results

    def on_eyedropper_toggle(self, checked: bool) -> None:
        if checked:
            self.tracking = TrackingState.ACTIVE
            self.api.current_view.viewport.set_cursor(Qt.CursorShape.CrossCursor)
        else:
            self.tracking = TrackingState.INACTIVE
            self.api.current_view.viewport.set_cursor(Qt.CursorShape.OpenHandCursor)

    def copy_row(
        self,
        row_name: str,
        labels_dict: dict[str, list[QLabel]],
        copy_btns_dict: dict[str, QToolButton],
        *_: Any,
    ) -> None:
        labels = labels_dict.get(row_name, [])
        values = [lbl.text() for lbl in labels if lbl.text() != "—"]

        if not values:
            return

        text = ", ".join(values)

        QApplication.clipboard().setText(text)
        QToolTip.showText(QCursor.pos(), "Copied!", copy_btns_dict[row_name])
        self.api.statusMessage.emit(f"Copied {text!r} to clipboard")
