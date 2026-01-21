"""Frame Properties Tool."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from logging import getLogger
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple

import vapoursynth as vs
from jetpytools import fallback
from pydantic import BaseModel
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QPalette, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHeaderView,
    QLabel,
    QMenu,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from vspackrgb.helpers import get_plane_buffer

from vsview.api import (
    AnimatedToggle,
    BaseGraphicsView,
    Checkbox,
    IconName,
    IconReloadMixin,
    LocalSettingsModel,
    PluginAPI,
    PluginBase,
    run_in_loop,
)
from vsview.app.plugins.api import VideoOutputProxy

from .builtins.field import FIELD_CATEGORY, FIELD_FORMATTERS
from .builtins.metrics import METRICS_CATEGORY, METRICS_FORMATTERS
from .builtins.video import VIDEO_CATEGORY, VIDEO_FORMATTERS
from .categories import CategoryRegistry
from .formatters import CompoundFormatterProperty, FormatterProperty, FormatterRegistry

# Item roles and types
ROLE_ITEM_TYPE = Qt.ItemDataRole.UserRole + 1
ROLE_RAW_DATA = Qt.ItemDataRole.UserRole + 2

ITEM_TYPE_CATEGORY = "category"
ITEM_TYPE_PROPERTY = "property"

logger = getLogger(__name__)


class RowData(NamedTuple):
    key: str
    value: str
    raw_key: str | list[str]
    raw_value: Any


class FramePropsViewMixin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def get_row_data(self: FramePropsTreeView | FramePropsTableView, index: QModelIndex) -> RowData:  # type: ignore[misc]
        model = self.model()
        row = index.row()
        parent = index.parent()

        name_index = model.index(row, 0, parent)
        value_index = model.index(row, 1, parent)

        name = name_index.data(Qt.ItemDataRole.DisplayRole)
        value = value_index.data(Qt.ItemDataRole.DisplayRole)

        raw_key = name_index.data(ROLE_RAW_DATA)
        raw_value = value_index.data(ROLE_RAW_DATA)

        return RowData(name, value, raw_key, raw_value)

    def _show_context_menu(self: FramePropsTreeView | FramePropsTableView, pos: QPoint) -> None:  # type: ignore[misc]
        if not (index := self.indexAt(pos)).isValid():
            return

        # TreeView specific: ignore categories
        if (item := self.current_model.itemFromIndex(index)) and item.data(ROLE_ITEM_TYPE) == ITEM_TYPE_CATEGORY:
            return

        data = self.get_row_data(index)

        menu = QMenu(self)

        if isinstance(data.raw_value, vs.VideoFrame):
            preview_action = QAction("Preview Frame", self)
            preview_action.triggered.connect(lambda: self.previewRequested.emit(data.raw_value, data.raw_key))
            menu.addAction(preview_action)
            menu.addSeparator()

        key_label = "Name" if isinstance(self, FramePropsTreeView) else "Key"

        copy_value_action = QAction("Copy Value", self)
        copy_value_action.triggered.connect(lambda: self._copy_to_clipboard(data.value, "value"))
        menu.addAction(copy_value_action)

        copy_key_action = QAction(f"Copy {key_label}", self)
        copy_key_action.triggered.connect(lambda: self._copy_to_clipboard(data.key, "key"))
        menu.addAction(copy_key_action)

        copy_row_action = QAction(f"Copy as {key_label}=Value", self)
        copy_row_action.triggered.connect(lambda: self._copy_to_clipboard(f"{data.key}={data.value}", "row"))
        menu.addAction(copy_row_action)

        if isinstance(self, FramePropsTreeView):
            menu.addSeparator()
            copy_original_action = QAction("Copy Original Key", self)
            copy_original_action.triggered.connect(lambda: self._copy_to_clipboard(str(data.raw_key), "original key"))
            menu.addAction(copy_original_action)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self: FramePropsTreeView | FramePropsTableView, text: str, description: str) -> None:  # type: ignore[misc]
        QApplication.clipboard().setText(text)

        logger.debug("Copied %s: %r", description, text)
        self.copyMessage.emit(f"Copied {description}: {text[:50]}{'...' if len(text) > 50 else ''}")


# PySide6 stubs are missing
if TYPE_CHECKING:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QStyleOptionViewItem as QQStyleOptionViewItem

    class QStyleOptionViewItem(QQStyleOptionViewItem):
        widget: QWidget
        rect: QRect
        fontMetrics: QFontMetrics
        features: QQStyleOptionViewItem.ViewItemFeature
        textElideMode: Qt.TextElideMode
else:
    from PySide6.QtWidgets import QStyleOptionViewItem


class WordWrapDelegate(QStyledItemDelegate):
    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:  # type: ignore[override]
        super().initStyleOption(option, index)
        if index.column() == 1:
            option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
            option.textElideMode = Qt.TextElideMode.ElideNone

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # type: ignore[override]
        size = super().sizeHint(option, index)

        if index.column() != 1:
            return size

        if not (text := index.data(Qt.ItemDataRole.DisplayRole)):
            return size

        if isinstance((view := option.widget), QTreeView):
            header = view.header()
        elif isinstance(view, QTableView):
            header = view.horizontalHeader()
        else:
            return size

        if (column_width := header.sectionSize(index.column())) <= 0:
            return size

        text_margin = view.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, option, view) + 1
        available_width = column_width - (2 * text_margin) - 4

        text_rect = option.fontMetrics.boundingRect(
            0,
            0,
            available_width,
            10000,
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
            str(text),
        )

        return QSize(size.width(), max(size.height(), text_rect.height() + 4))


class FramePropsModel(QStandardItemModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHorizontalHeaderLabels(["Key", "Value"])
        self.category_items = dict[str, QStandardItem]()

        CategoryRegistry.register(VIDEO_CATEGORY, METRICS_CATEGORY, FIELD_CATEGORY)
        FormatterRegistry.register(VIDEO_FORMATTERS, METRICS_FORMATTERS, FIELD_FORMATTERS)

    def add_prop(self, key: str, value: Any, category: str | None = None) -> None:
        formatted_key, formatted_value = FormatterRegistry.format_property(key, value)

        key_item = self._create_item(formatted_key, ITEM_TYPE_PROPERTY, key)
        value_item = self._create_item(formatted_value, ITEM_TYPE_PROPERTY, value)

        self._add_to_category(key_item, value_item, key, category)

    def add_compound_prop(
        self,
        compound: CompoundFormatterProperty,
        props: Mapping[str, Any],
        category: str | None = None,
    ) -> None:
        if not any(key in props for key in compound.prop_keys):
            return

        formatted_value = compound.format_value(props)

        first_key = compound.prop_keys[0]
        key_item = self._create_item(compound.display_name, ITEM_TYPE_PROPERTY, compound.prop_keys)
        value_item = self._create_item(formatted_value, ITEM_TYPE_PROPERTY, formatted_value)

        self._add_to_category(key_item, value_item, first_key, category)

    def _add_to_category(
        self,
        key_item: QStandardItem,
        value_item: QStandardItem,
        ref_key: str,
        category: str | None = None,
    ) -> None:
        if category is None:
            category = CategoryRegistry.get_category(ref_key)

        if category not in self.category_items:
            category_item = self._create_item(category, ITEM_TYPE_CATEGORY)
            empty_value = self._create_item("", ITEM_TYPE_CATEGORY)
            self.appendRow([category_item, empty_value])
            self.category_items[category] = category_item

        self.category_items[category].appendRow([key_item, value_item])

    def load_props(self, props: Mapping[str, Any]) -> None:
        self.clear_props()

        added_compounds = set[tuple[str, ...]]()

        # Group properties by category
        categories = defaultdict[str, list[str]](list)
        for key in props:
            if not FormatterRegistry.is_consumed_by_compound(key):
                categories[CategoryRegistry.get_category(key)].append(key)

        # Track compound formatters by category
        compound_categories = defaultdict[str, list[CompoundFormatterProperty]](list)
        for compound in FormatterRegistry.compound_formatters:
            compound_categories[CategoryRegistry.get_category(compound.prop_keys[0])].append(compound)

        all_categories = categories.keys() | compound_categories.keys()

        # Sort categories by order (highest first)
        sorted_categories = sorted(all_categories, key=lambda c: CategoryRegistry.get_category_order(c), reverse=True)

        for category in sorted_categories:
            items = list[tuple[int, str | CompoundFormatterProperty]]()

            for key in categories.get(category, []):
                order = FormatterRegistry.get_property_order(key)
                items.append((order, key))

            for compound in compound_categories.get(category, []):
                if compound.prop_keys not in added_compounds:
                    order = FormatterRegistry.get_property_order(compound.prop_keys[0])

                    items.append((order, compound))
                    added_compounds.add(compound.prop_keys)

            items.sort(key=lambda x: x[0], reverse=True)

            for _, item in items:
                if isinstance(item, CompoundFormatterProperty):
                    self.add_compound_prop(item, props, category)
                else:
                    self.add_prop(item, props[item], category)

    def clear_props(self) -> None:
        self.removeRows(0, self.rowCount())
        self.category_items.clear()

    def _create_item(self, text: str, item_type: str, raw_data: Any | None = None) -> QStandardItem:
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData(item_type, ROLE_ITEM_TYPE)
        if raw_data is not None:
            item.setData(raw_data, ROLE_RAW_DATA)
        return item


class FramePropsTreeView(QTreeView, FramePropsViewMixin):
    copyMessage = Signal(str)
    previewRequested = Signal(object, str)  # (VideoFrame, title)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        FramePropsViewMixin.__init__(self)

        self.current_model = FramePropsModel(self)
        self.setModel(self.current_model)

        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setExpandsOnDoubleClick(True)
        self.setAnimated(True)
        self.setIndentation(self.indentation() // 2)

        self.setItemDelegate(WordWrapDelegate(self))

        header = self.header()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(150)
        header.sectionResized.connect(self._on_section_resized)

        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    @run_in_loop(return_future=False)
    def update_props(self, props: Mapping[str, Any]) -> None:
        self.current_model.load_props(props)
        self.expandAll()
        self.resizeColumnToContents(0)

    def _on_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if logical_index == 1 and old_size != new_size:
            self.doItemsLayout()


class FramePropsTableModel(QStandardItemModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHorizontalHeaderLabels(["Key", "Value"])

    def load_props(self, props: Mapping[str, Any]) -> None:
        self.clear_rows()

        sorted_keys = sorted(props.keys(), key=lambda x: (not x.startswith("_"), x))

        for key in sorted_keys:
            value = props[key]
            formatted_value = FormatterProperty.default_format(value, repr_frame=True)

            key_item = self._create_item(key, key)
            value_item = self._create_item(formatted_value, value)

            self.appendRow([key_item, value_item])

    def clear_rows(self) -> None:
        self.removeRows(0, self.rowCount())

    def _create_item(self, text: str, raw_data: Any) -> QStandardItem:
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData(raw_data, ROLE_RAW_DATA)
        return item


class FramePropsTableView(QTableView, FramePropsViewMixin):
    copyMessage = Signal(str)
    previewRequested = Signal(object, str)  # (frame, title)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        FramePropsViewMixin.__init__(self)

        self.current_model = FramePropsTableModel(self)
        self.setModel(self.current_model)

        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setItemDelegate(WordWrapDelegate(self))

        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setVisible(False)

        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    @run_in_loop(return_future=False)
    def update_props(self, props: Mapping[str, Any]) -> None:
        self.current_model.load_props(props)


class FramePropPreviewGraphicsView(BaseGraphicsView):
    def __init__(self, parent: QWidget | None, api: PluginAPI) -> None:
        super().__init__(parent)
        self.api = api
        self._f2c_cache = dict[int, vs.VideoNode]()
        self.api.register_on_destroy(self._f2c_cache.clear)

    def set_frame(self, frame: vs.VideoFrame) -> None:
        with frame:
            qimage = self.frame_to_qimage(frame)

        self.pixmap_item.setPixmap(QPixmap.fromImage(qimage))

        if self.autofit:
            self.set_zoom(0)

    def frame_to_qimage(self, frame: vs.VideoFrame) -> QImage:
        match frame.format.id:
            case vs.GRAY8:
                fmt = QImage.Format.Format_Grayscale8
            case vs.GRAY16:
                fmt = QImage.Format.Format_Grayscale16
            case _:
                with self.api.vs_context():
                    packed_clip = self.api.packer.pack_clip(self.frame2clip(frame))

                    with packed_clip.get_frame(0) as packed:
                        return self.api.packer.frame_to_qimage(packed).copy()

        return QImage(
            get_plane_buffer(frame, 0),  # type: ignore[call-overload]
            frame.width,
            frame.height,
            frame.get_stride(0),
            fmt,
        ).copy()

    def frame2clip(self, frame: vs.VideoFrame) -> vs.VideoNode:
        key = hash((frame.width, frame.height, frame.format.id))

        if key not in self._f2c_cache:
            self._f2c_cache[key] = vs.core.std.BlankClip(
                width=frame.width, height=frame.height, format=frame.format, keep=True
            )

        frame_cp = frame.copy()
        return vs.core.std.ModifyFrame(self._f2c_cache[key], self._f2c_cache[key], lambda n, f: frame_cp)


class GlobalSettings(BaseModel):
    pretty: Annotated[
        bool,
        Checkbox(
            label="Pretty",
            text="Enable pretty display by default",
            tooltip="Enable the pretty display by default",
        ),
    ] = True


class LocalSettings(LocalSettingsModel):
    pretty: bool | None = None


class FramePropsPlugin(PluginBase[GlobalSettings, LocalSettings], IconReloadMixin):
    identifier = "jet_vsview_frameprops"
    display_name = "Frame Props"

    current_preview_key: str

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        IconReloadMixin.__init__(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.toolbar = QToolBar(self)
        self.toolbar.setMovable(False)
        self.toolbar.setStyleSheet("QToolBar { border: none; background: transparent; spacing: 4px; }")

        self.pretty_toggle = AnimatedToggle(self)
        self.pretty_toggle.setToolTip("Toggle between pretty and raw display")

        self.toolbar.addWidget(QLabel("Pretty", self))
        self.toolbar.addWidget(self.pretty_toggle)

        spacer = QWidget(self.toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)

        nav_icon_states = {
            (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ButtonText,
            (QIcon.Mode.Disabled, QIcon.State.Off): QPalette.ColorRole.PlaceholderText,
        }

        self.prev_btn = self.make_tool_button(
            IconName.ARROW_LEFT,
            "Previous frame in history",
            self,
            icon_size=QSize(24, 24),
            icon_states=nav_icon_states,
        )
        self.prev_btn.clicked.connect(self._on_prev_clicked)

        self.history_combo = QComboBox(self)
        self.history_combo.setToolTip("Select a frame from history")
        self.history_combo.setMinimumWidth(150)
        self.history_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.history_combo.currentIndexChanged.connect(self._on_history_selected)

        self.next_btn = self.make_tool_button(
            IconName.ARROW_RIGHT,
            "Next frame in history",
            self,
            icon_size=QSize(24, 24),
            icon_states=nav_icon_states,
        )
        self.next_btn.clicked.connect(self._on_next_clicked)

        self.toolbar.addWidget(self.prev_btn)
        self.toolbar.addWidget(self.history_combo)
        self.toolbar.addWidget(self.next_btn)

        layout.addWidget(self.toolbar)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)

        self.stack = QStackedWidget(self.splitter)

        self.raw_table = FramePropsTableView(self.stack)
        self.pretty_tree = FramePropsTreeView(self.stack)
        self.raw_table.copyMessage.connect(self.status_message)
        self.pretty_tree.copyMessage.connect(self.status_message)

        self.raw_table.previewRequested.connect(self._show_preview)
        self.pretty_tree.previewRequested.connect(self._show_preview)

        self.stack.addWidget(self.raw_table)
        self.stack.addWidget(self.pretty_tree)

        self.pretty_toggle.toggled.connect(self.stack.setCurrentIndex)
        self.pretty_toggle.setChecked(fallback(self.settings.local_.pretty, self.settings.global_.pretty))
        self.pretty_toggle.toggled.connect(lambda checked: self.update_local_settings(pretty=not not checked))  # noqa: SIM208

        self.splitter.addWidget(self.stack)

        self.preview_container = QWidget(self.splitter)
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(2)

        preview_header = QToolBar(self.preview_container)
        preview_header.setMovable(False)

        self.preview_label = QLabel("Preview", self.preview_container)
        preview_header.addWidget(self.preview_label)

        preview_spacer = QWidget(preview_header)
        preview_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        preview_header.addWidget(preview_spacer)

        close_btn = self.make_tool_button(
            IconName.X_CIRCLE,
            "Close preview",
            self.preview_container,
            icon_size=QSize(16, 16),
        )
        close_btn.clicked.connect(self._hide_preview)
        preview_header.addWidget(close_btn)

        preview_layout.addWidget(preview_header)

        self.preview_view = FramePropPreviewGraphicsView(self.preview_container, self.api)
        self.preview_view.set_autofit(True)
        self.preview_view.setMinimumWidth(200)
        preview_layout.addWidget(self.preview_view)

        self.splitter.addWidget(self.preview_container)

        self.splitter.setSizes([1, 0])

        layout.addWidget(self.splitter)

        self.register_icon_button(close_btn, IconName.X_CIRCLE, QSize(16, 16))
        self.register_icon_button(self.prev_btn, IconName.ARROW_LEFT, QSize(24, 24), icon_states=nav_icon_states)
        self.register_icon_button(self.next_btn, IconName.ARROW_RIGHT, QSize(24, 24), icon_states=nav_icon_states)

        self.api.register_on_destroy(close_btn.click)

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        self._hide_preview()
        return super().on_current_voutput_changed(voutput, tab_index)

    def on_current_frame_changed(self, n: int) -> None:
        self.update_history_ui(n)

    def on_playback_started(self) -> None:
        self.history_combo.setEnabled(False)
        self.history_combo.hidePopup()
        self.update_nav_buttons()

    def on_playback_stopped(self) -> None:
        self.history_combo.setEnabled(True)
        self.update_nav_buttons()

    @run_in_loop(return_future=False)
    def update_history_ui(self, current_frame: int) -> None:
        if current_frame not in (props_cache := self.api.current_voutput.props):
            return

        available_frames = list(props_cache.keys())
        props = props_cache[current_frame]

        with QSignalBlocker(self.history_combo):
            self.history_combo.clear()

            for frame in sorted(available_frames):
                label = f"Frame {frame} (Current)" if frame == current_frame else f"Frame {frame}"
                self.history_combo.addItem(label, frame)

        current_index = -1
        for i in range(self.history_combo.count()):
            if self.history_combo.itemData(i) == current_frame:
                current_index = i
                break

        if current_index >= 0:
            self.history_combo.setCurrentIndex(current_index)

        self.update_views(props)
        self.update_nav_buttons()
        self.refresh_preview(current_frame)

    def update_views(self, props: Mapping[str, Any]) -> None:
        self.pretty_tree.update_props(props)
        self.raw_table.update_props(props)

    def update_nav_buttons(self) -> None:
        if not self.history_combo.isEnabled():
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        current_index = self.history_combo.currentIndex()
        self.prev_btn.setEnabled(current_index > 0)
        self.next_btn.setEnabled(current_index < self.history_combo.count() - 1)

    def refresh_preview(self, current_frame: int) -> None:
        if self.splitter.sizes()[1] == 0:
            return

        props = self.api.current_voutput.props[current_frame]
        key = self.current_preview_key

        if (key := self.current_preview_key) in props and isinstance(props[key], vs.VideoFrame):
            self.preview_view.set_frame(props[key])

    def status_message(self, message: str) -> None:
        if len(msg_lines := message.split("\n")) > 1:
            self.api.statusMessage.emit(f"{self.display_name}: {msg_lines[0]}...")
        else:
            self.api.statusMessage.emit(f"{self.display_name}: {message}")

    def _on_history_selected(self, index: int) -> None:
        if index < 0:
            return

        if (frame := self.history_combo.itemData(index)) is not None and frame in self.api.current_voutput.props:
            props = self.api.current_voutput.props[frame]
            self.update_views(props)
            self.update_nav_buttons()
            self.refresh_preview(frame)

    def _on_prev_clicked(self) -> None:
        current_index = self.history_combo.currentIndex()
        if current_index > 0:
            self.history_combo.setCurrentIndex(current_index - 1)

    def _on_next_clicked(self) -> None:
        current_index = self.history_combo.currentIndex()
        if current_index < self.history_combo.count() - 1:
            self.history_combo.setCurrentIndex(current_index + 1)

    def _show_preview(self, frame: vs.VideoFrame, key_name: str) -> None:
        self.current_preview_key = key_name

        self.preview_label.setText(f"Preview: {key_name!r}")
        self.preview_view.set_frame(frame)

        self.splitter.setSizes([int(self.splitter.height() * 0.6), int(self.splitter.height() * 0.4)])

    @run_in_loop(return_future=False)
    def _hide_preview(self) -> None:
        self.splitter.setSizes([1, 0])
        self.preview_view.reset_scene()

        with suppress(AttributeError):
            del self.current_preview_key
