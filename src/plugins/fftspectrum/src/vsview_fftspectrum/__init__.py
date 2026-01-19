from typing import Annotated, Any, Literal

from pydantic import BaseModel
from PySide6.QtWidgets import QHBoxLayout, QWidget
from vapoursynth import VideoNode
from vstools import ColorRange, DitherType, depth, initialize_input, join, split, stack_planes

from vsview.api import Checkbox, Dropdown, PluginAPI, PluginBase, PluginGraphicsView, PluginSettings, Spin, hookimpl


class GlobalSettings(BaseModel):
    autofit: Annotated[
        bool,
        Checkbox(
            label="Auto fit",
            text="Enable autofit by default",
            tooltip="Enable autofit by default when opening the plugin tab",
        ),
    ] = True

    mode: Annotated[
        Literal["h", "v"],
        Dropdown(
            label="Mode",
            items=[("Horizontal", "h"), ("Vertical", "v")],
            tooltip="Stacking direction",
        ),
    ] = "v"
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
    ] = 2


class FFTSpectrumPlugin(PluginBase[GlobalSettings]):
    identifier = "jet_vsview_fftspectrum"
    display_name = "FFT Spectrum"

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        self.view = FFTSpectrumView(self, self.api, self.settings)
        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(0)
        self.current_layout.addWidget(self.view)

        self.api.globalSettingsChanged.connect(self.on_settings_changed)

    def on_settings_changed(self) -> None:
        self.view.set_autofit(self.settings.global_.autofit)
        self.view.refresh()


class FFTSpectrumView(PluginGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings]) -> None:
        super().__init__(parent, api)
        self.settings = settings
        self.autofit = self.settings.global_.autofit

    @initialize_input(bits=32)
    def get_node(self, clip: VideoNode) -> VideoNode:
        planes = split(clip)

        if len(planes) == 1:
            return planes[0].fftspectrum_rs.FFTSpectrum()

        def fft(p: VideoNode, text: str) -> VideoNode:
            p = p.fftspectrum_rs.FFTSpectrum()

            if self.settings.global_.write_plane_name:
                p = p.text.Text(text, self.settings.global_.alignment, self.settings.global_.scale)

            return p

        planes = [fft(p, k) for k, p in zip(clip.format.name, planes)]

        # TODO: Add grid frequencies

        clip_fft = join(planes, clip.format.color_family)
        stacked = stack_planes(clip_fft, False, False, self.settings.global_.mode)

        return depth(stacked, 8, range_out=ColorRange.FULL, dither_type=DitherType.NONE)


@hookimpl
def vsview_register_toolpanel() -> type[PluginBase[Any, Any]]:
    return FFTSpectrumPlugin


@hookimpl
def vsview_register_tooldock() -> type[PluginBase[Any, Any]]:
    return FFTSpectrumPlugin
