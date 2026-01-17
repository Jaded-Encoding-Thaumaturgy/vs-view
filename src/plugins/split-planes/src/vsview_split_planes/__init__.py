from math import copysign
from typing import Annotated, Any, Literal, assert_never

from jetpytools import fallback
from pydantic import BaseModel
from PySide6.QtWidgets import QBoxLayout, QDoubleSpinBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget, QWidgetAction
from vapoursynth import VideoNode
from vstools import scale_mask, stack_planes

from vsview.api import (
    Checkbox,
    Dropdown,
    LocalSettingsModel,
    PluginAPI,
    PluginBase,
    PluginGraphicsView,
    PluginSettings,
    SegmentedControl,
    Spin,
    hookimpl,
)


class GlobalSettings(BaseModel):
    autofit: Annotated[
        bool,
        Checkbox(
            label="Auto fit",
            text="Enable autofit by default",
            tooltip="Enable autofit by default when opening the plugin tab",
        ),
    ] = False

    shift_float_chroma: Annotated[
        bool,
        Checkbox(
            label="Shift float chroma",
            text="",
            tooltip="If checked, shift U and V by +0.5 when working in float YUV formats",
        ),
    ] = True
    offset_chroma: Annotated[
        float | Literal["min", "max"],
        Dropdown(
            label="Offset chroma",
            items=[("Fixed", 0.0), ("Min", "min"), ("Max", "max")],
            tooltip="Apply chroma plane offseting\n"
            '- "min": match luma minimum\n'
            '- "max": match luma maximum\n'
            "- Fixed: use a custom float value",
        ),
    ] = 0.0
    mode: Annotated[
        Literal["h", "v"],
        Dropdown(
            label="Mode",
            items=[("Horizontal", "h"), ("Vertical", "v")],
            tooltip="Stacking direction",
        ),
    ] = "h"
    write_plane_name: Annotated[
        bool,
        Checkbox(
            label="Write plane name",
            text="",
            tooltip='If checked, overlays the short plane name ("Y", "U", "V", "R", "G", ...) on each plane.',
        ),
    ] = True
    alignment: Annotated[
        int,
        Spin(
            label="Alignment",
            min=1,
            max=9,
            tooltip='Text alignment for plane labels (only used if "Write plane name" is checked`).',
        ),
    ] = 7
    scale: Annotated[
        int,
        Spin(
            label="Scale",
            min=1,
            tooltip='Font scale for plane labels (only used if "Write plane name" is checked`).',
        ),
    ] = 1


class LocalSettings(LocalSettingsModel):
    autofit: bool | None = None
    offset_chroma: float | Literal["min", "max"] | None = None


class SplitPlanesPlugin(PluginBase[GlobalSettings, LocalSettings]):
    identifier = "jet_vsview_split_planes"
    display_name = "Split Planes"

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        self.view = SplitPlanesView(self, self.api, self.settings)
        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(0)
        self.current_layout.addWidget(self.view)

        self.api.globalSettingsChanged.connect(self.on_settings_changed)

    def on_settings_changed(self) -> None:
        self.view.autofit = fallback(self.settings.local_.autofit, self.settings.global_.autofit)
        self.view.refresh(self)
        self.view.sync_offset_controls_from_settings()


class SplitPlanesView(PluginGraphicsView):
    def __init__(
        self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, LocalSettings]
    ) -> None:
        super().__init__(parent, api)
        self.settings = settings

        self.context_menu.addSeparator()

        # Container widget for the offset chroma controls
        self._offset_container = QWidget(self.context_menu)
        layout = QVBoxLayout(self._offset_container)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # Label
        label = QLabel("Offset Chroma", self._offset_container)
        layout.addWidget(label)

        # Segmented control for Min/Max/Fixed
        self.offset_segment = SegmentedControl(
            ["Fixed", "Min", "Max"],
            self._offset_container,
            QBoxLayout.Direction.LeftToRight,
        )
        layout.addWidget(self.offset_segment)

        # Spinbox for "Fixed" mode
        self.fixed_spinbox_container = QWidget(self._offset_container)
        spinbox_layout = QHBoxLayout(self.fixed_spinbox_container)
        spinbox_layout.setContentsMargins(0, 0, 0, 0)
        spinbox_layout.setSpacing(4)

        self.offset_spinbox = QDoubleSpinBox(self.fixed_spinbox_container)
        self.offset_spinbox.setRange(-1.0, 1.0)
        self.offset_spinbox.setSingleStep(0.1)
        self.offset_spinbox.setDecimals(3)
        self.offset_spinbox.setValue(0.0)
        self.offset_spinbox.setToolTip("Fixed offset value for chroma planes")
        spinbox_layout.addWidget(self.offset_spinbox)

        layout.addWidget(self.fixed_spinbox_container)

        # Add container to context menu via QWidgetAction
        action = QWidgetAction(self.context_menu)
        action.setDefaultWidget(self._offset_container)
        self.context_menu.addAction(action)

        # Connect signals
        self.offset_segment.segmentChanged.connect(self.on_offset_segment_changed)
        self.offset_spinbox.valueChanged.connect(self.on_offset_spinbox_changed)

        # Set initial state from global settings
        self.sync_offset_controls_from_settings()

        if self.settings.local_.autofit:
            self.autofit_action.trigger()

    def parent(self) -> SplitPlanesPlugin:
        if isinstance(parent := super().parent(), SplitPlanesPlugin):
            return parent

        raise NotImplementedError

    def get_node(self, clip: VideoNode) -> VideoNode:
        if (offset_chroma := self.settings.local_.offset_chroma) is None:
            offset = False
        elif isinstance(offset_chroma, (float, int)):
            offset = scale_mask(abs(offset_chroma), 32, clip)
            offset = copysign(offset, offset_chroma)
        else:
            offset = offset_chroma

        return stack_planes(
            clip,
            self.settings.global_.shift_float_chroma,
            offset,
            self.settings.global_.mode,
            self.settings.global_.write_plane_name,
            self.settings.global_.alignment,
            self.settings.global_.scale,
        )

    def sync_offset_controls_from_settings(self) -> None:
        match offset := self.settings.local_.offset_chroma:
            case int() | float() | None:
                self.offset_segment.index = 0
                self.fixed_spinbox_container.setEnabled(True)
                self.offset_spinbox.setValue(float(offset or 0))
            case "min":
                self.offset_segment.index = 1
                self.fixed_spinbox_container.setEnabled(False)
            case "max":
                self.offset_segment.index = 2
                self.fixed_spinbox_container.setEnabled(False)
            case _:
                assert_never(offset)

    def on_offset_segment_changed(self, index: int) -> None:
        self.fixed_spinbox_container.setEnabled(index == 0)
        parent = self.parent()

        match index:
            case 0:
                parent.update_local_settings(offset_chroma=self.offset_spinbox.value())
            case 1:
                parent.update_local_settings(offset_chroma="min")
            case 2:
                parent.update_local_settings(offset_chroma="max")
            case _:
                raise NotImplementedError

        self.refresh(parent)

    def on_offset_spinbox_changed(self, value: float) -> None:
        if self.offset_segment.index == 0:
            parent = self.parent()
            parent.update_local_settings(offset_chroma=value)
            self.refresh(parent)

    def _on_autofit_action(self) -> None:
        super()._on_autofit_action()

        self.parent().update_local_settings(autofit=self.autofit)


@hookimpl
def vsview_register_toolpanel() -> type[PluginBase[Any, Any]]:
    return SplitPlanesPlugin


@hookimpl
def vsview_register_tooldock() -> type[PluginBase[Any, Any]]:
    return SplitPlanesPlugin
