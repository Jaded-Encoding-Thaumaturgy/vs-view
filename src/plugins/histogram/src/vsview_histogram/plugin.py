from typing import override

from jetpytools import fallback
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vsview.api import PluginAPI, VideoOutputProxy, WidgetPluginBase, run_in_loop

from .levels import HistogramContainerWidget
from .settings import GlobalSettings
from .vectorscope import VectorscopeContainerWidget
from .waveform import WaveformContainerWidget


class HistogramPlugin(WidgetPluginBase[GlobalSettings]):
    identifier = "jet_vsview_histogram"
    display_name = "Histogram"

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        self.tab_widget = QTabWidget(self)
        self.setup_levels()
        self.setup_vectorscope()
        self.setup_waveform()

        # Set default tab
        self.tab_widget.setCurrentIndex(self.settings.global_.selected_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.tab_widget)

    def setup_levels(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Levels controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 8, 8, 4)

        bin_label = QLabel("Bin resolution:", container)
        controls_layout.addWidget(bin_label)

        self.levels_bin_combo = QComboBox(container)
        self.levels_bin_combo.addItem("Auto (Width-based)", 0)
        self.levels_bin_combo.addItem("256 bins", 256)
        self.levels_bin_combo.addItem("512 bins", 512)
        self.levels_bin_combo.addItem("1024 bins", 1024)
        self.levels_bin_combo.setToolTip(
            "Target number of histogram bins.\n'Auto' dynamically scales based on panel width."
        )
        self.levels_bin_combo.setCurrentIndex(self.levels_bin_combo.findData(self.settings.global_.levels.bin_res))
        self.levels_bin_combo.currentIndexChanged.connect(self.on_levels_bin_resolution_changed)
        controls_layout.addWidget(self.levels_bin_combo)

        factor_label = QLabel("Clamp factor", container)
        controls_layout.addWidget(factor_label)

        self.levels_factor_spin = QDoubleSpinBox(
            container,
            suffix=" %",
            decimals=3,
            minimum=0.001,
            maximum=100.0,
            singleStep=0.001,
            value=self.settings.global_.levels.factor,
        )
        self.levels_factor_spin.setToolTip(
            "Clamping threshold for peak pixel counts\nto make smaller peaks visible (0.001% to 100%)"
        )
        self.levels_factor_spin.valueChanged.connect(self.on_levels_factor_changed)
        controls_layout.addWidget(self.levels_factor_spin)

        self.levels_unsafe_checkbox = QCheckBox("Show unsafe zones", container)
        self.levels_unsafe_checkbox.setChecked(self.settings.global_.levels.show_unsafe)
        self.levels_unsafe_checkbox.setToolTip("Highlight broadcast unsafe ranges in YUV format.")
        self.levels_unsafe_checkbox.stateChanged.connect(self.on_levels_unsafe_zones_changed)
        controls_layout.addWidget(self.levels_unsafe_checkbox)
        controls_layout.addStretch()

        self.levels_container = HistogramContainerWidget(container, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.levels_container)

        self.tab_widget.addTab(container, "Levels")

    def setup_vectorscope(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Vectorscope controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 8, 8, 4)

        mode_label = QLabel("Vectorscope mode:", container)
        controls_layout.addWidget(mode_label)

        self.vectorscope_mode_combo = QComboBox(container)
        self.vectorscope_mode_combo.addItem("Density", "density")
        self.vectorscope_mode_combo.addItem("Chroma Wheel", "chroma_wheel")
        self.vectorscope_mode_combo.addItem("Pixel Color", "pixel_color")
        self.vectorscope_mode_combo.setCurrentIndex(
            self.vectorscope_mode_combo.findData(self.settings.global_.vectorscope.mode)
        )
        self.vectorscope_mode_combo.currentIndexChanged.connect(self.on_vectorscope_mode_changed)
        controls_layout.addWidget(self.vectorscope_mode_combo)

        res_label = QLabel("Resolution:", container)
        controls_layout.addWidget(res_label)

        self.vectorscope_res_combo = QComboBox(container)
        self.vectorscope_res_combo.addItem("Auto", 0)
        self.vectorscope_res_combo.addItem("256x256", 256)
        self.vectorscope_res_combo.addItem("512x512", 512)
        self.vectorscope_res_combo.addItem("1024x1024", 1024)
        self.vectorscope_res_combo.setCurrentIndex(
            self.vectorscope_res_combo.findData(self.settings.global_.vectorscope.res)
        )
        self.vectorscope_res_combo.currentIndexChanged.connect(self.on_vectorscope_resolution_changed)
        controls_layout.addWidget(self.vectorscope_res_combo)

        luma_label = QLabel("Luma:", container)
        controls_layout.addWidget(luma_label)

        self.vectorscope_luma_spin = QSpinBox(
            container,
            minimum=0,
            maximum=255,
            value=self.settings.global_.vectorscope.luma,
        )
        self.vectorscope_luma_spin.valueChanged.connect(self.on_vectorscope_luma_changed)
        self.vectorscope_luma_spin.setEnabled(self.settings.global_.vectorscope.mode == "chroma_wheel")
        controls_layout.addWidget(self.vectorscope_luma_spin)
        controls_layout.addStretch()

        self.vectorscope_container = VectorscopeContainerWidget(self, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.vectorscope_container)

        self.tab_widget.addTab(container, "Vectorscope")

    def setup_waveform(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Waveform controls
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 8, 8, 4)

        mode_label = QLabel("Waveform mode:", container)
        controls_layout.addWidget(mode_label)

        self.waveform_mode_combo = QComboBox(container)
        self.waveform_mode_combo.addItem("Luma", "luma")
        self.waveform_mode_combo.addItem("RGB/YUV Parade", "parade")
        self.waveform_mode_combo.setCurrentIndex(self.waveform_mode_combo.findData(self.settings.global_.waveform.mode))
        self.waveform_mode_combo.currentIndexChanged.connect(self.on_waveform_mode_changed)
        controls_layout.addWidget(self.waveform_mode_combo)

        res_label = QLabel("Resolution:", container)
        controls_layout.addWidget(res_label)

        self.waveform_res_combo = QComboBox(container)
        self.waveform_res_combo.addItem("Auto", 0)
        self.waveform_res_combo.addItem("256 lines", 256)
        self.waveform_res_combo.addItem("512 lines", 512)
        self.waveform_res_combo.addItem("1024 lines", 1024)
        self.waveform_res_combo.setCurrentIndex(self.waveform_res_combo.findData(self.settings.global_.waveform.res))
        self.waveform_res_combo.currentIndexChanged.connect(self.on_waveform_resolution_changed)
        controls_layout.addWidget(self.waveform_res_combo)

        self.waveform_zones_checkbox = QCheckBox("Show zones", container)
        self.waveform_zones_checkbox.setChecked(self.settings.global_.waveform.show_zones)
        self.waveform_zones_checkbox.stateChanged.connect(self.on_waveform_unsafe_changed)
        controls_layout.addWidget(self.waveform_zones_checkbox)

        self.waveform_dynamic_checkbox = QCheckBox("Dynamic gain", container)
        self.waveform_dynamic_checkbox.setChecked(self.settings.global_.waveform.dynamic_gain)
        self.waveform_dynamic_checkbox.stateChanged.connect(self.on_waveform_dynamic_gain_changed)
        controls_layout.addWidget(self.waveform_dynamic_checkbox)

        gain_label = QLabel("Gain:", container)
        controls_layout.addWidget(gain_label)

        self.waveform_gain_spin = QDoubleSpinBox(
            container,
            suffix="x",
            decimals=1,
            minimum=0.1,
            maximum=10.0,
            singleStep=0.1,
            value=self.settings.global_.waveform.gain,
        )
        self.waveform_gain_spin.valueChanged.connect(self.on_waveform_gain_changed)
        controls_layout.addWidget(self.waveform_gain_spin)
        controls_layout.addStretch()

        self.waveform_container = WaveformContainerWidget(container, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.waveform_container)

        self.tab_widget.addTab(container, "Waveform")

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_histogram()

    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        self.update_histogram(voutput=voutput)

    @override
    def on_current_frame_changed(self, n: int) -> None:
        self.update_histogram(n)

    @override
    def on_playback_stopped(self) -> None:
        self.update_histogram()

    @run_in_loop(return_future=False)
    def update_histogram(self, n: int | None = None, voutput: VideoOutputProxy | None = None) -> None:
        if self.api.is_playing:
            return

        n = fallback(n, self.api.current_frame)
        voutput = fallback(voutput, self.api.current_voutput)

        active_tab = self.tab_widget.currentIndex()

        with self.api.vs_context(), voutput.vs_output.clip.get_frame(n) as frame:
            if active_tab == 0:
                self.levels_container.update_histogram(frame)
            elif active_tab == 1:
                self.vectorscope_container.update_histogram(frame)
            elif active_tab == 2:
                self.waveform_container.update_histogram(frame)

    def on_tab_changed(self, index: int) -> None:
        self.settings.global_.selected_tab = index
        self.update_histogram()

    def on_levels_bin_resolution_changed(self, index: int) -> None:
        self.settings.global_.levels.bin_res = self.levels_bin_combo.currentData()
        self.update_histogram()

    def on_levels_factor_changed(self, value: float) -> None:
        self.settings.global_.levels.factor = value
        self.update_histogram()

    def on_levels_unsafe_zones_changed(self, state: int) -> None:
        self.settings.global_.levels.show_unsafe = self.levels_unsafe_checkbox.isChecked()
        self.update_histogram()

    def on_waveform_unsafe_changed(self, state: int) -> None:
        self.settings.global_.waveform.show_zones = self.waveform_zones_checkbox.isChecked()
        self.update_histogram()

    def on_waveform_dynamic_gain_changed(self, state: int) -> None:
        self.settings.global_.waveform.dynamic_gain = self.waveform_dynamic_checkbox.isChecked()
        self.update_histogram()

    def on_waveform_gain_changed(self, value: float) -> None:
        self.settings.global_.waveform.gain = value
        self.update_histogram()

    def on_waveform_mode_changed(self, index: int) -> None:
        self.settings.global_.waveform.mode = self.waveform_mode_combo.currentData()
        self.update_histogram()

    def on_waveform_resolution_changed(self, index: int) -> None:
        self.settings.global_.waveform.res = self.waveform_res_combo.currentData()
        self.update_histogram()

    def on_vectorscope_mode_changed(self, index: int) -> None:
        mode = self.vectorscope_mode_combo.currentData()
        self.settings.global_.vectorscope.mode = mode
        self.vectorscope_luma_spin.setEnabled(mode == "chroma_wheel")
        self.update_histogram()

    def on_vectorscope_resolution_changed(self, index: int) -> None:
        self.settings.global_.vectorscope.res = self.vectorscope_res_combo.currentData()
        self.update_histogram()

    def on_vectorscope_luma_changed(self, value: int) -> None:
        self.settings.global_.vectorscope.luma = value
        self.update_histogram()
