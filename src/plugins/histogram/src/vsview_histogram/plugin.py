import itertools
from logging import getLogger
from threading import Lock
from typing import override

import numpy as np
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
from vsengine import UnifiedFuture

from vsview.api import PluginAPI, WidgetPluginBase, run_in_background, run_in_loop

from .levels import HistogramContainerWidget
from .luma import LumaContainerWidget
from .settings import GlobalSettings
from .vectorscope import VectorscopeContainerWidget
from .waveform import WaveformContainerWidget

logger = getLogger(__name__)


class HistogramPlugin(WidgetPluginBase[GlobalSettings]):
    identifier = "jet_vsview_histogram"
    display_name = "Histogram"

    numba_prewarm_worker: UnifiedFuture[None] | None = None
    lock = Lock()

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        self.tab_widget = QTabWidget(self)
        self.setup_levels()
        self.setup_luma()
        self.setup_vectorscope()
        self.setup_waveform()

        # Set default tab
        self.tab_widget.setCurrentIndex(self.settings.global_.selected_tab)
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.tab_widget)

        # Start Numba JIT background warming thread
        with self.lock:
            if HistogramPlugin.numba_prewarm_worker is None:
                HistogramPlugin.numba_prewarm_worker = prewarm_numba()
        HistogramPlugin.numba_prewarm_worker.map(lambda _: self._notify_numba_ready())

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

        self.levels_container = HistogramContainerWidget(container, self.api, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.levels_container)

        self.tab_widget.addTab(container, "Levels")

    def setup_luma(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(8, 8, 8, 4)

        shift_label = QLabel("Frequency (Shift):", container)
        controls_layout.addWidget(shift_label)

        self.luma_shift_combo = QComboBox(container)
        for i in range(1, 8 + 1):
            self.luma_shift_combo.addItem(f"{2**i} cycles ({i})", i)
        self.luma_shift_combo.setToolTip("Controls the number of luma cycles displayed across the scope.")
        self.luma_shift_combo.setCurrentIndex(self.luma_shift_combo.findData(self.settings.global_.luma.shift))
        self.luma_shift_combo.currentIndexChanged.connect(self.on_luma_shift_changed)
        controls_layout.addWidget(self.luma_shift_combo)

        self.luma_sawtooth_checkbox = QCheckBox("Sawtooth style", container)
        self.luma_sawtooth_checkbox.setChecked(self.settings.global_.luma.sawtooth)
        self.luma_sawtooth_checkbox.setToolTip("Switches the rendering style from sine-like to a sawtooth waveform.")
        self.luma_sawtooth_checkbox.stateChanged.connect(self.on_luma_sawtooth_changed)
        controls_layout.addWidget(self.luma_sawtooth_checkbox)
        controls_layout.addStretch()

        self.luma_container = LumaContainerWidget(container, self.api, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.luma_container)

        self.tab_widget.addTab(container, "Luma")

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
        self.vectorscope_mode_combo.setToolTip(
            "Display mode for vectorscope plots:\n"
            "- Density: logarithmic heat-map with phosphor color table\n"
            "- Chroma Wheel: density map drawn over a full-color UV background wheel\n"
            "- Pixel Color: each pixel plotted at its actual RGB-converted color"
        )
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
        self.vectorscope_res_combo.setToolTip(
            "Size of the internal scope image (square).\n'Auto' caps to the bit depth limit."
        )
        self.vectorscope_res_combo.setCurrentIndex(
            self.vectorscope_res_combo.findData(self.settings.global_.vectorscope.res)
        )
        self.vectorscope_res_combo.currentIndexChanged.connect(self.on_vectorscope_resolution_changed)
        controls_layout.addWidget(self.vectorscope_res_combo)

        matrix_label = QLabel("Matrix:", container)
        controls_layout.addWidget(matrix_label)

        self.vectorscope_matrix_combo = QComboBox(container)
        self.vectorscope_matrix_combo.addItem("Auto", "auto")
        self.vectorscope_matrix_combo.addItem("BT.709", "bt709")
        self.vectorscope_matrix_combo.addItem("BT.601", "bt601")
        self.vectorscope_matrix_combo.addItem("BT.2020", "bt2020")
        self.vectorscope_matrix_combo.addItem("ST 240M", "st240m")
        self.vectorscope_matrix_combo.setToolTip(
            "Color matrix coefficients used for target graticules and signal conversion.\n"
            "'Auto' detects from clip properties or resolution."
        )
        self.vectorscope_matrix_combo.setCurrentIndex(
            self.vectorscope_matrix_combo.findData(self.settings.global_.vectorscope.matrix)
        )
        self.vectorscope_matrix_combo.currentIndexChanged.connect(self.on_vectorscope_matrix_changed)
        controls_layout.addWidget(self.vectorscope_matrix_combo)

        luma_label = QLabel("Luma:", container)
        controls_layout.addWidget(luma_label)

        self.vectorscope_luma_spin = QSpinBox(
            container,
            minimum=0,
            maximum=255,
            value=self.settings.global_.vectorscope.luma,
        )
        self.vectorscope_luma_spin.setToolTip(
            "Fixed luma value used for color reconstruction in Chroma Wheel mode.\nOnly active in Chroma Wheel mode."
        )
        self.vectorscope_luma_spin.valueChanged.connect(self.on_vectorscope_luma_changed)
        self.vectorscope_luma_spin.setEnabled(self.settings.global_.vectorscope.mode == "chroma_wheel")
        controls_layout.addWidget(self.vectorscope_luma_spin)
        controls_layout.addStretch()

        self.vectorscope_container = VectorscopeContainerWidget(self, self.api, self.settings)
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
        self.waveform_mode_combo.setToolTip(
            "Waveform rendering layout:\n"
            "- Luma: single Y-plane waveform\n"
            "- RGB/YUV Parade: one waveform per plane side-by-side"
        )
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
        self.waveform_res_combo.setToolTip("Vertical resolution of the scope.\n'Auto' caps to the bit depth limit.")
        self.waveform_res_combo.setCurrentIndex(self.waveform_res_combo.findData(self.settings.global_.waveform.res))
        self.waveform_res_combo.currentIndexChanged.connect(self.on_waveform_resolution_changed)
        controls_layout.addWidget(self.waveform_res_combo)

        self.waveform_zones_checkbox = QCheckBox("Show zones", container)
        self.waveform_zones_checkbox.setChecked(self.settings.global_.waveform.show_zones)
        self.waveform_zones_checkbox.setToolTip(
            "Overlay neutral lines and broadcast-limit lines\n"
            "(16/235 for luma, 16/240 for chroma) with shaded unsafe regions."
        )
        self.waveform_zones_checkbox.stateChanged.connect(self.on_waveform_unsafe_changed)
        controls_layout.addWidget(self.waveform_zones_checkbox)

        self.waveform_dynamic_checkbox = QCheckBox("Dynamic gain", container)
        self.waveform_dynamic_checkbox.setChecked(self.settings.global_.waveform.dynamic_gain)
        self.waveform_dynamic_checkbox.setToolTip(
            "When enabled, scales brightness relative to the densest column.\n"
            "When disabled, scales relative to the frame height."
        )
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
        self.waveform_gain_spin.setToolTip("Brightness multiplier applied on top of the logarithmic scale.")
        self.waveform_gain_spin.valueChanged.connect(self.on_waveform_gain_changed)
        controls_layout.addWidget(self.waveform_gain_spin)
        controls_layout.addStretch()

        self.waveform_container = WaveformContainerWidget(container, self.api, self.settings)
        layout.addLayout(controls_layout)
        layout.addWidget(self.waveform_container)

        self.tab_widget.addTab(container, "Waveform")

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update_histogram()

    @override
    def on_current_frame_changed(self, n: int) -> None:
        self.update_histogram(n)

    @override
    def on_playback_stopped(self) -> None:
        self.update_histogram()

    @run_in_loop(return_future=False)
    def update_histogram(self, n: int | None = None) -> None:
        if not self.isVisible() or self.api.is_playing or not self.isEnabled():
            return

        n = fallback(n, self.api.current_frame)

        active_tab = self.tab_widget.currentIndex()

        with (
            self.api.blocker(self),
            self.api.vs_context(),
            self.api.current_voutput.vs_output.clip.get_frame(n) as frame,
        ):
            if active_tab == 0:
                self.levels_container.update_histogram(frame)
            # The API does the update for us
            elif active_tab == 1:
                pass
            elif active_tab == 2:
                self.vectorscope_container.update_histogram(frame)
            elif active_tab == 3:
                self.waveform_container.update_histogram(frame)

    def on_tab_changed(self, index: int) -> None:
        self.settings.global_.selected_tab = index
        if index == 1:
            self.luma_container.view.refresh()
        else:
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

    def on_vectorscope_matrix_changed(self, index: int) -> None:
        self.settings.global_.vectorscope.matrix = self.vectorscope_matrix_combo.currentData()
        self.update_histogram()

    def on_vectorscope_luma_changed(self, value: int) -> None:
        self.settings.global_.vectorscope.luma = value
        self.update_histogram()

    def on_luma_shift_changed(self, index: int) -> None:
        self.settings.global_.luma.shift = self.luma_shift_combo.currentData()
        self.luma_container.view.refresh()

    def on_luma_sawtooth_changed(self, state: int) -> None:
        self.settings.global_.luma.sawtooth = self.luma_sawtooth_checkbox.isChecked()
        self.luma_container.view.refresh()

    def _notify_numba_ready(self) -> None:
        self.luma_container.view.numba_ready = True
        self.luma_container.view.refresh()


@run_in_background(name="NumbaPreWarm")
def prewarm_numba() -> None:
    from .luma.numba_backend import process_luma_numba

    logger.debug("Starting pre-warm of process_luma_numba...")
    dtypes = [(np.uint8, 8), (np.uint16, 16), (np.float32, 16)]
    sawtooth_options = [False, True]
    is_limited_options = [False, True]

    for (dtype, bits), sawtooth, is_limited in itertools.product(dtypes, sawtooth_options, is_limited_options):
        # Covers contiguous and non-contiguous layouts for uint8, uint16, and float32
        logger.debug("Pre-warm of dtype=%s, bits=%s, sawtooth=%s, is_limited=%s", dtype, bits, sawtooth, is_limited)
        # Contiguous variant
        dummy_src = np.zeros((16, 16), dtype=dtype)
        dummy_dst = np.zeros((16, 16), dtype=np.uint8)
        process_luma_numba(dummy_src, dummy_dst, bits, 4, sawtooth, is_limited)

        # Non-contiguous (strided) variant
        dummy_src_nc = np.zeros((32, 32), dtype=dtype)[::2, ::2]
        process_luma_numba(dummy_src_nc, dummy_dst, bits, 4, sawtooth, is_limited)

        logger.debug("Pre-warm numba process_luma_numba is completed")
