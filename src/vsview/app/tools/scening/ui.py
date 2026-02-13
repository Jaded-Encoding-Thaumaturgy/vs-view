from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import copy
from datetime import timedelta
from enum import IntEnum
from typing import Any, Self

from jetpytools import cachedproperty, to_arr
from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    QTime,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QCursor, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QLineEdit,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from vsview.api import FrameEdit, IconName, PluginAPI, Time, TimeEdit, VideoOutputProxy
from vsview.assets.utils import load_icon

from .models import AbstractRange, RangeFrame, RangeTime, SceneRow

ROLE_CHECK_STATE = Qt.ItemDataRole.UserRole + 10


class HeaderIntEnum(IntEnum):
    header_name: str

    def __new__(cls, value: int, header_name: str = "") -> Self:
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.header_name = header_name
        return obj


class Col(HeaderIntEnum):
    COLOR = 0, "Color"
    NAME = 1, "Name"
    OUTPUTS = 2, "Outputs"
    DISPLAY = 3, "Display"
    DELETE = 4, ""


class SceneTableModel(QAbstractTableModel):
    """Table model for scene rows (Color | Name | Outputs | Display | Delete)."""

    SceneRowRole = Qt.ItemDataRole.UserRole + 1
    scenesModified = Signal()
    sceneDisplayModified = Signal(SceneRow)
    sceneColorModified = Signal(SceneRow)
    sceneCheckOutputsModified = Signal(SceneRow)

    def __init__(self, output_map: dict[int, str], parent: QWidget) -> None:
        super().__init__(parent)
        self.scenes = list[SceneRow]()
        self.output_map = output_map

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.scenes)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(Col)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.scenes)):
            return None

        scene = self.scenes[index.row()]
        col = Col(index.column())

        match role:
            case Qt.ItemDataRole.DisplayRole:
                if col == Col.NAME:
                    return scene.name
                if col == Col.OUTPUTS:
                    return (
                        "All Outputs"
                        if not scene.checked_outputs
                        else ", ".join(
                            self.output_map.get(idx, f"Output {idx}") for idx in sorted(scene.checked_outputs)
                        )
                    )

            case Qt.ItemDataRole.EditRole if col == Col.NAME:
                return scene.name

            case Qt.ItemDataRole.DecorationRole if col == Col.COLOR:
                return scene.color

            # NOTE: We intentionally ignore Qt.ItemDataRole.CheckStateRole
            # for the Display column because we draw it manually

            case _ if role == ROLE_CHECK_STATE and col == Col.DISPLAY:
                return Qt.CheckState.Checked if scene.display else Qt.CheckState.Unchecked

            case Qt.ItemDataRole.TextAlignmentRole:
                if col == Col.DISPLAY:
                    return Qt.AlignmentFlag.AlignCenter
                if col == Col.DELETE:
                    return Qt.AlignmentFlag.AlignCenter

                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

            case self.SceneRowRole:
                return scene

        return None

    def setData(
        self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self.scenes)):
            return False

        scene = self.scenes[index.row()]
        col = Col(index.column())

        match role:
            case Qt.ItemDataRole.EditRole if col == Col.NAME:
                scene.name = str(value)

            case _ if role == ROLE_CHECK_STATE and col == Col.DISPLAY:
                scene.display = value == Qt.CheckState.Checked.value
                self.sceneDisplayModified.emit(scene)

            case Qt.ItemDataRole.DecorationRole if col == Col.COLOR and isinstance(value, QColor):
                scene.color = value
                self.sceneColorModified.emit(scene)

            case Qt.ItemDataRole.UserRole if col == Col.OUTPUTS and isinstance(value, set):
                scene.checked_outputs = value
                self.sceneCheckOutputsModified.emit(scene)

            case _:
                return False

        self.dataChanged.emit(index, index)
        self.scenesModified.emit()
        return True

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

        return base | Qt.ItemFlag.ItemIsEditable if Col(index.column()) == Col.NAME else base

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        return (
            Col(section).header_name
            if (
                orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(Col)
            )
            else None
        )

    def add_scene(self, scene: SceneRow, emit_signal: bool = True) -> QModelIndex:
        row = len(self.scenes)

        self.beginInsertRows(QModelIndex(), row, row)
        self.scenes.append(scene)
        self.endInsertRows()

        if emit_signal:
            self.scenesModified.emit()
        return self.index(row, 0)

    def remove_scene(self, row: int | Sequence[int]) -> None:
        if isinstance(row, Sequence):
            start, end = min(row), max(row)
        else:
            start, end = row, row

        self.beginRemoveRows(QModelIndex(), start, end)
        for r in to_arr(row):
            self.scenes.pop(r)
        self.endRemoveRows()

        self.scenesModified.emit()


class NonClosingMenu(QMenu):
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (action := self.actionAt(event.position().toPoint())) and action.isCheckable():
            return action.trigger()

        super().mouseReleaseEvent(event)


class SceneTableDelegate(QStyledItemDelegate):
    SWATCH_SIZE = 16
    DELETE_ICON_SIZE = QSize(16, 16)

    colorChosen = Signal(QModelIndex)

    def __init__(self, outputs_map: dict[int, str], parent: QWidget) -> None:
        super().__init__(parent)
        self.output_map = outputs_map

    @cachedproperty
    def delete_pixmap(self) -> QPixmap:
        return load_icon(IconName.X_CIRCLE, self.DELETE_ICON_SIZE, QColor("#e74c3c"))

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        self.initStyleOption(option, index)

        # Background
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif isinstance(bg := index.data(Qt.ItemDataRole.BackgroundRole), QColor):
            painter.fillRect(option.rect, bg)
        painter.restore()

        # Content drawing per column
        match Col(index.column()):
            case Col.COLOR:
                self._paint_color(painter, option, index)
            case Col.NAME | Col.OUTPUTS:
                self._paint_text(painter, option, index)
            case Col.DISPLAY:
                self._paint_checkbox(painter, option, index)
            case Col.DELETE:
                self._paint_delete(painter, option, index)

    def _paint_text(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if not (text := option.text):
            return

        painter.save()
        color_role = (
            option.palette.highlightedText()
            if option.state & QStyle.StateFlag.State_Selected
            else option.palette.text()
        )
        painter.setPen(color_role.color())

        alignment = index.data(Qt.ItemDataRole.TextAlignmentRole) or (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        painter.drawText(option.rect.adjusted(4, 0, -4, 0), alignment, text)
        painter.restore()

    def _paint_color(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if isinstance(color := index.data(Qt.ItemDataRole.DecorationRole), QColor):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(color)
            painter.setPen(QColor("#555"))
            painter.drawRoundedRect(self._center_rect(option.rect, self.SWATCH_SIZE), 3, 3)
            painter.restore()

    def _paint_checkbox(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        style = option.widget.style() if option.widget else QApplication.style()
        check_opt = QStyleOptionViewItem(option)

        is_checked = index.data(ROLE_CHECK_STATE) == Qt.CheckState.Checked
        check_opt.state |= QStyle.StateFlag.State_Enabled | (
            QStyle.StateFlag.State_On if is_checked else QStyle.StateFlag.State_Off
        )

        size = max(style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, option, option.widget), 14)
        check_opt.rect = self._center_rect(option.rect, size)

        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, check_opt, painter, option.widget)

    def _paint_delete(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        pm = self.delete_pixmap
        painter.drawPixmap(self._center_rect(option.rect, pm.width()).topLeft(), pm)

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        match Col(index.column()), event.type():
            case Col.DISPLAY, QEvent.Type.MouseButtonPress | QEvent.Type.MouseButtonDblClick:
                new_state = (
                    Qt.CheckState.Unchecked.value
                    if index.data(ROLE_CHECK_STATE) == Qt.CheckState.Checked
                    else Qt.CheckState.Checked.value
                )
                model.setData(index, new_state, ROLE_CHECK_STATE)
                return True

            case Col.DELETE, QEvent.Type.MouseButtonPress | QEvent.Type.MouseButtonDblClick:
                if isinstance(model, SceneTableModel):
                    model.remove_scene(index.row())
                return True

            case Col.DISPLAY | Col.DELETE, QEvent.Type.MouseButtonRelease:
                return True

            case Col.COLOR, QEvent.Type.MouseButtonRelease:
                chosen = QColorDialog.getColor(index.data(Qt.ItemDataRole.DecorationRole), option.widget, "Scene Color")

                if chosen.isValid():
                    model.setData(index, chosen, Qt.ItemDataRole.DecorationRole)
                    self.colorChosen.emit(index)

                return True

            case Col.OUTPUTS, QEvent.Type.MouseButtonRelease:
                self._open_outputs_menu(index, option.widget)
                return True

        return super().editorEvent(event, model, option, index)

    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        return QLineEdit(parent) if Col(index.column()) == Col.NAME else super().createEditor(parent, option, index)

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        if Col(index.column()) == Col.NAME and isinstance(editor, QLineEdit):
            value = index.data(Qt.ItemDataRole.EditRole)
            editor.setText(str(value) if value else "")
            editor.selectAll()
            return

        super().setEditorData(editor, index)

    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if Col(index.column()) == Col.NAME and isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
            return

        super().setModelData(editor, model, index)

    @staticmethod
    def _center_rect(cell_rect: QRect, size: int) -> QRect:
        # Center a square rect of `size` inside `cell_rect`
        return QRect(
            cell_rect.x() + (cell_rect.width() - size) // 2,
            cell_rect.y() + (cell_rect.height() - size) // 2,
            size,
            size,
        )

    def _open_outputs_menu(self, index: QModelIndex | QPersistentModelIndex, widget: QWidget | None) -> None:
        if not widget or not isinstance(scene := index.data(SceneTableModel.SceneRowRole), SceneRow):
            return

        menu = NonClosingMenu(widget)
        all_action = menu.addAction("All Outputs")
        all_action.setCheckable(True)
        menu.addSeparator()

        # Track explicitly created output actions to avoid messy loop filtering later
        output_actions = list[QAction]()
        for vsindex, vs_name in self.output_map.items():
            action = menu.addAction(vs_name)
            action.setCheckable(True)
            action.setData(vsindex)
            action.setChecked(vsindex in scene.checked_outputs)
            output_actions.append(action)

        all_action.setChecked(not scene.checked_outputs)

        # Connect signals
        all_action.toggled.connect(lambda checked: self._on_all_action_toggle(checked, output_actions))

        for act in output_actions:
            act.toggled.connect(lambda checked: all_action.setChecked(False) if checked else None)

        # Block and show menu
        menu.exec(QCursor.pos())

        # Collect results
        new_checked = set() if all_action.isChecked() else {a.data() for a in output_actions if a.isChecked()}

        if index.model():
            index.model().setData(index, new_checked, Qt.ItemDataRole.UserRole)

        menu.deleteLater()

    def _on_all_action_toggle(self, checked: bool, output_actions: list[QAction]) -> None:
        if checked:
            for action in output_actions:
                action.setChecked(False)


class RangeCol(HeaderIntEnum):
    START_FRAME = 0, "Start Frame"
    END_FRAME = 1, "End Frame"
    START_TIME = 2, "Start Time"
    END_TIME = 3, "End Time"
    LABEL = 4, "Label"


class RangeTableModel(QAbstractTableModel):
    RangeRole = Qt.ItemDataRole.UserRole + 1
    SceneRowRole = Qt.ItemDataRole.UserRole + 2
    rangesModified = Signal()
    rangeDataModified = Signal(AbstractRange, SceneRow)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent)
        self._data = list[tuple[RangeFrame | RangeTime, SceneRow]]()
        self.api = api
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.api.register_on_destroy(lambda: cachedproperty.clear_cache(self))

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self._data)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(RangeCol)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        range_item, scene = self._data[index.row()]
        col = RangeCol(index.column())
        v = self.current_voutput

        match role:
            case Qt.ItemDataRole.DisplayRole:
                match col:
                    case RangeCol.LABEL:
                        return range_item.label
                    case RangeCol.START_FRAME:
                        return str(range_item.as_frames(v)[0])
                    case RangeCol.END_FRAME:
                        return str(range_item.as_frames(v)[1])
                    case RangeCol.START_TIME:
                        return range_item.as_times(v)[0].to_ts("{H:02d}:{M:02d}:{S:02d}.{ms:03d}")
                    case RangeCol.END_TIME:
                        return range_item.as_times(v)[1].to_ts("{H:02d}:{M:02d}:{S:02d}.{ms:03d}")

            case Qt.ItemDataRole.EditRole:
                match col:
                    case RangeCol.LABEL:
                        return range_item.label
                    case RangeCol.START_FRAME:
                        return range_item.as_frames(v)[0]
                    case RangeCol.END_FRAME:
                        return range_item.as_frames(v)[1]
                    case RangeCol.START_TIME:
                        return range_item.as_times(v)[0]
                    case RangeCol.END_TIME:
                        return range_item.as_times(v)[1]

            case Qt.ItemDataRole.BackgroundRole:
                color = copy(scene.color)
                color.setAlphaF(color.alphaF() * 0.25)
                return color

            case Qt.ItemDataRole.TextAlignmentRole:
                return (
                    Qt.AlignmentFlag.AlignCenter
                    if col != RangeCol.LABEL
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )

            case self.RangeRole:
                return range_item

            case self.SceneRowRole:
                return scene

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return False

        range_item, scene_row = self._data[index.row()]
        col = RangeCol(index.column())
        v = self.current_voutput

        if role == Qt.ItemDataRole.EditRole:
            match col:
                case RangeCol.LABEL:
                    range_item.label = str(value)
                case RangeCol.START_FRAME:
                    range_item.from_frames(int(value), None, v)
                case RangeCol.END_FRAME:
                    range_item.from_frames(None, int(value), v)
                case RangeCol.START_TIME:
                    range_item.from_times(Time.from_qtime(value), None, v)
                case RangeCol.END_TIME:
                    range_item.from_times(None, Time.from_qtime(value), v)

            if col in (RangeCol.START_FRAME, RangeCol.START_TIME):
                self.dataChanged.emit(
                    self.index(index.row(), RangeCol.START_FRAME), self.index(index.row(), RangeCol.START_TIME)
                )
            elif col in (RangeCol.END_FRAME, RangeCol.END_TIME):
                self.dataChanged.emit(
                    self.index(index.row(), RangeCol.END_FRAME), self.index(index.row(), RangeCol.END_TIME)
                )
            self.rangeDataModified.emit(range_item, scene_row)
            self.rangesModified.emit()
            return True

        return False

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(RangeCol)
        ):
            return RangeCol(section).header_name
        return None

    @property
    def ranges(self) -> list[tuple[RangeFrame | RangeTime, SceneRow]]:
        return self._data

    @property
    def current_voutput(self) -> VideoOutputProxy:
        return self.api.current_voutput

    @contextmanager
    def insert_rows(self, row: int, count: int) -> Iterator[None]:
        self.beginInsertRows(QModelIndex(), row, row + count - 1)
        try:
            yield
        finally:
            self.endInsertRows()

    @contextmanager
    def remove_rows(self, row: int, count: int) -> Iterator[None]:
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        try:
            yield
        finally:
            self.endRemoveRows()

    @contextmanager
    def reset_model(self) -> Iterator[None]:
        self.beginResetModel()
        try:
            yield
        finally:
            self.endResetModel()

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort_column = column
        self._sort_order = order
        self._apply_sort()

    def set_scenes(self, scenes: list[SceneRow]) -> None:
        with self.reset_model():
            self._data.clear()

            for scene in scenes:
                for r in scene.ranges:
                    self._data.append((r, scene))

            if self._sort_column >= 0:
                self._data.sort(key=self._sort_key, reverse=self._sort_order == Qt.SortOrder.DescendingOrder)

    def add_range(self, range_item: RangeFrame | RangeTime, scene: SceneRow) -> None:
        row = len(self._data)

        with self.insert_rows(row, 1):
            scene.ranges.append(range_item)
            self._data.append((range_item, scene))

        self.rangesModified.emit()

    def remove_range(self, idx: QModelIndex) -> None:
        if not (0 <= (i := idx.row()) < len(self._data)):
            return

        with self.remove_rows(i, 1):
            range_item, scene = self._data.pop(i)
            scene.ranges = [r for r in scene.ranges if r is not range_item]

        self.rangesModified.emit()

    def _sort_key(self, item: tuple[RangeFrame | RangeTime, SceneRow]) -> Any:
        range_item, _ = item
        v = self.current_voutput

        match RangeCol(self._sort_column):
            case RangeCol.START_FRAME | RangeCol.START_TIME:
                return range_item.as_frames(v)[0]
            case RangeCol.END_FRAME | RangeCol.END_TIME:
                return range_item.as_frames(v)[1]
            case RangeCol.LABEL:
                return range_item.label.lower()

    def _apply_sort(self) -> None:
        if self._sort_column < 0 or not self._data:
            return

        self.layoutAboutToBeChanged.emit()
        self._data.sort(key=self._sort_key, reverse=self._sort_order == Qt.SortOrder.DescendingOrder)
        self.layoutChanged.emit()


class RangeTableDelegate(QStyledItemDelegate):
    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        col = RangeCol(index.column())

        match col:
            case RangeCol.START_FRAME | RangeCol.END_FRAME:
                editor = FrameEdit(parent)
                editor.setButtonSymbols(FrameEdit.ButtonSymbols.NoButtons)

                if isinstance(model := index.model(), RangeTableModel):
                    editor.setMaximum(model.current_voutput.vs_output.clip.num_frames - 1)

            case RangeCol.START_TIME | RangeCol.END_TIME:
                editor = TimeEdit(parent)

            case RangeCol.LABEL:
                editor = QLineEdit(parent)

        editor.setAutoFillBackground(True)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        col = RangeCol(index.column())
        value = index.data(Qt.ItemDataRole.EditRole)

        match col:
            case RangeCol.START_FRAME | RangeCol.END_FRAME:
                if isinstance(editor, FrameEdit):
                    editor.setValue(int(value))
                    return

            case RangeCol.START_TIME | RangeCol.END_TIME:
                if isinstance(editor, TimeEdit) and isinstance(value, timedelta):
                    if isinstance(value, Time):
                        editor.setTime(value.to_qtime())
                    else:
                        editor.setTime(QTime.fromMSecsSinceStartOfDay(int(value.total_seconds() * 1000)))
                    return

            case RangeCol.LABEL:
                if isinstance(editor, QLineEdit):
                    editor.setText(str(value))
                    return

        super().setEditorData(editor, index)

    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        match RangeCol(index.column()):
            case RangeCol.START_FRAME | RangeCol.END_FRAME:
                if isinstance(editor, FrameEdit):
                    model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)
                    return

            case RangeCol.START_TIME | RangeCol.END_TIME:
                if isinstance(editor, TimeEdit):
                    model.setData(index, editor.time(), Qt.ItemDataRole.EditRole)
                    return

            case RangeCol.LABEL:
                if isinstance(editor, QLineEdit):
                    model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)
                    return

        super().setModelData(editor, model, index)
