from __future__ import annotations

from logging import getLogger
from typing import override

from jetpytools import clamp
from PySide6.QtCore import QRect, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from vsview.api import (
    ActionDefinition,
    IconName,
    IconReloadMixin,
    PluginAPI,
    VideoOutputProxy,
    WidgetPluginBase,
    run_in_loop,
)

from .settings import GlobalSettings
from .utils import CommandLabel, CropValues, CustomRect, CustomSize

logger = getLogger(__name__)


class RegionSelectorPlugin(WidgetPluginBase[GlobalSettings], IconReloadMixin):
    identifier = "jet_vsview_regionselector"
    display_name = "Region Selector"

    shortcuts = (ActionDefinition(f"{identifier}.toggle", "Toggle region selector", "C"),)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        IconReloadMixin.__init__(self)

        self.crop_rect = QRect()

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(8)

        self.setup_ui()
        self.setup_shortcuts()

        self.api.globalSettingsChanged.connect(self._on_global_settings_changed)
        self.api.register_on_destroy(lambda: setattr(self.api.current_view, "rect_selection_enabled", False))

    def setup_ui(self) -> None:
        self.enable_btn = self.make_tool_button(
            IconName.FRAME_CORNERS,
            "Toggle region selector",
            self,
            checkable=True,
            icon_size=QSize(24, 24),
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.enable_btn.toggled.connect(self.on_region_toggled)

        self.clear_btn = self.make_tool_button(
            IconName.X_CIRCLE,
            "Clear selection",
            self,
            icon_size=QSize(24, 24),
            icon_states=self.DEFAULT_ICON_STATES,
        )
        self.clear_btn.clicked.connect(self.clear_selection)

        top_bar_layout = QHBoxLayout()
        top_bar_layout.addWidget(self.enable_btn)
        top_bar_layout.addWidget(self.clear_btn)
        top_bar_layout.addStretch()
        self.main_layout.addLayout(top_bar_layout)

        self.info_group = QGroupBox("Selection Details", self)
        self.info_grid = QGridLayout(self.info_group)
        self.info_grid.setContentsMargins(8, 8, 8, 8)
        self.info_grid.setSpacing(8)

        self.left_spin = self._add_spin_field("Left:", 0, 0)
        self.right_spin = self._add_spin_field("Right:", 0, 1)
        self.top_spin = self._add_spin_field("Top:", 1, 0)
        self.bottom_spin = self._add_spin_field("Bottom:", 1, 1)
        self.width_spin = self._add_spin_field("Width:", 2, 0)
        self.height_spin = self._add_spin_field("Height:", 2, 1)

        self.main_layout.addWidget(self.info_group)

        self.cmd_group = QGroupBox(
            "Command" + ("s" if len(self.settings.global_.code_format) > 1 else ""),
            self,
        )
        self.cmd_group_layout = QVBoxLayout(self.cmd_group)
        self.cmd_labels = list[CommandLabel]()
        self._setup_cmd_labels()

        self.main_layout.addWidget(self.cmd_group)
        self.main_layout.addStretch()

    def setup_shortcuts(self) -> None:
        self.api.register_shortcut(
            f"{self.identifier}.toggle",
            self.enable_btn.toggle,
            self,
            context=Qt.ShortcutContext.WindowShortcut,
        )

    def _add_spin_field(self, text: str, row: int, column: int) -> QSpinBox:
        name_label = QLabel(text, self.info_group)
        font = name_label.font()
        font.setBold(True)
        name_label.setFont(font)

        spin = QSpinBox(self.info_group)
        spin.setSingleStep(self.settings.global_.mod)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(self._on_crop_spin_changed)

        self.info_grid.addWidget(name_label, row, column * 2)
        self.info_grid.addWidget(spin, row, column * 2 + 1)
        return spin

    def _setup_cmd_labels(self) -> None:
        for btn in self.cmd_group.findChildren(QToolButton):
            btn.deleteLater()

        for lbl in self.cmd_group.findChildren(CommandLabel):
            lbl.deleteLater()

        self.cmd_labels.clear()

        for code_fmt in self.settings.global_.code_format:
            cmd_label = CommandLabel(code_fmt, self.cmd_group)
            self.cmd_labels.append(cmd_label)

            copy_btn = self.make_tool_button(
                IconName.CLIPBOARD,
                "Copy command to clipboard",
                self.cmd_group,
                icon_states=self.DEFAULT_ICON_STATES,
            )
            copy_btn.clicked.connect(
                lambda checked, label=cmd_label, btn=copy_btn: self.copy_to_clipboard(checked, label, btn)
            )

            cmd_layout = QHBoxLayout()
            cmd_layout.addWidget(cmd_label)
            cmd_layout.addWidget(copy_btn)
            self.cmd_group_layout.addLayout(cmd_layout)

    # Plugin API Hooks
    @override
    def on_hide(self) -> None:
        if self.enable_btn.isChecked():
            self.enable_btn.setChecked(False)

    @run_in_loop(return_future=False)
    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        # TODO: Add support for HDR
        if voutput.packer.hdr:
            self.setDisabled(True)
            logger.warning("The Cropping tool isn't available for HDR outputs.")
        else:
            self.setEnabled(True)
        self.api.current_view.rect_selection_enabled = self.enable_btn.isChecked()
        self._apply_view_selection(self.api.current_view.rect_selection)

    @override
    def on_view_rect_selection_changed(self, rect: QRect) -> None:
        self._apply_view_selection(rect)

    @override
    def on_view_rect_selection_finished(self, rect: QRect) -> None:
        self._apply_view_selection(rect)

    # Hook
    def clear_selection(self) -> None:
        self.api.current_view.clear_rect_selection()

    def on_region_toggled(self, checked: bool) -> None:
        self.api.current_view.rect_selection_enabled = checked

        if checked:
            self._apply_view_selection(self.api.current_view.rect_selection)

    def copy_to_clipboard(self, checked: bool, label: QLabel, btn: QToolButton) -> None:
        QApplication.clipboard().setText(label.text())
        QToolTip.showText(QCursor.pos(), "Copied!", btn)
        self.api.statusMessage.emit(f"Copied to clipboard: {label.text()}")

    def _on_global_settings_changed(self) -> None:
        self.left_spin.setSingleStep(self.settings.global_.mod)
        self.right_spin.setSingleStep(self.settings.global_.mod)
        self.top_spin.setSingleStep(self.settings.global_.mod)
        self.bottom_spin.setSingleStep(self.settings.global_.mod)
        self.width_spin.setSingleStep(self.settings.global_.mod)
        self.height_spin.setSingleStep(self.settings.global_.mod)

        self._setup_cmd_labels()
        self._apply_view_selection(self.api.current_view.rect_selection)

    @run_in_loop(return_future=False)
    def _apply_view_selection(self, rect: QRect) -> None:
        image_size = CustomSize.from_clip(self.api.current_voutput.vs_output.clip)

        if rect.isEmpty():
            snapped_rect = QRect()
        else:
            snapped_rect = CustomRect(rect)
            snapped_rect.sanitize(image_size, self.settings.global_.mod)

        if rect != snapped_rect:
            self.api.current_view.set_rect_selection(snapped_rect)
            return

        self.crop_rect = snapped_rect
        self._update_fields()

    def _update_fields(self) -> None:
        image_size = CustomSize.from_clip(self.api.current_voutput.vs_output.clip)
        image_w = image_size.width()
        image_h = image_size.height()

        if self.crop_rect.isEmpty():
            values = CropValues()

            for label in self.cmd_labels:
                label.reset_text()
        else:
            values = CropValues.from_rect(self.crop_rect, image_size)

            for label in self.cmd_labels:
                label.format(**values._asdict())

        left_max = max(image_w - values.right - 1, 0)
        right_max = max(image_w - values.left - 1, 0)
        top_max = max(image_h - values.bottom - 1, 0)
        bottom_max = max(image_h - values.top - 1, 0)
        width_max = max(values.width, image_w - values.left)
        height_max = max(values.height, image_h - values.top)

        with (
            QSignalBlocker(self.left_spin),
            QSignalBlocker(self.top_spin),
            QSignalBlocker(self.right_spin),
            QSignalBlocker(self.bottom_spin),
            QSignalBlocker(self.width_spin),
            QSignalBlocker(self.height_spin),
        ):
            self.left_spin.setRange(0, left_max)
            self.right_spin.setRange(0, right_max)
            self.top_spin.setRange(0, top_max)
            self.bottom_spin.setRange(0, bottom_max)
            self.width_spin.setRange(0, width_max)
            self.height_spin.setRange(0, height_max)

            self.left_spin.setValue(min(values.left, left_max))
            self.right_spin.setValue(min(values.right, right_max))
            self.top_spin.setValue(min(values.top, top_max))
            self.bottom_spin.setValue(min(values.bottom, bottom_max))
            self.width_spin.setValue(min(values.width, width_max))
            self.height_spin.setValue(min(values.height, height_max))

    def _on_crop_spin_changed(self, value: int) -> None:
        image_size = CustomSize.from_clip(self.api.current_voutput.vs_output.clip)
        image_w = image_size.width()
        image_h = image_size.height()

        left = self.left_spin.value()
        top = self.top_spin.value()
        right = self.right_spin.value()
        bottom = self.bottom_spin.value()

        if self.sender() == self.width_spin:
            right = image_w - left - self.width_spin.value()
        elif self.sender() == self.height_spin:
            bottom = image_h - top - self.height_spin.value()

        right = clamp(right, 0, image_w - left - 1)
        bottom = clamp(bottom, 0, image_h - top - 1)

        rect = CustomRect.from_crop(left, top, right, bottom, image_size, self.settings.global_.mod)
        self.api.current_view.set_rect_selection(rect, finished=True)
