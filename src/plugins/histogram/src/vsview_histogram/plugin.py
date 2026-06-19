from typing import override

from jetpytools import fallback
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from vsview.api import PluginAPI, VideoOutputProxy, WidgetPluginBase, run_in_loop

from .levels import HistogramContainerWidget
from .settings import GlobalSettings


class HistogramPlugin(WidgetPluginBase[GlobalSettings]):
    identifier = "jet_vsview_histogram"
    display_name = "Histogram"

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        # Container for stacked charts
        self.container = HistogramContainerWidget(self, self.settings)

        # Controls layout
        self.controls_layout = QHBoxLayout()
        self.controls_layout.setContentsMargins(8, 8, 8, 4)

        self.bin_label = QLabel("Bin resolution:", self)
        self.controls_layout.addWidget(self.bin_label)

        self.bin_combo = QComboBox(self)
        self.bin_combo.addItem("Auto (Width-based)", 0)
        self.bin_combo.addItem("256 bins", 256)
        self.bin_combo.addItem("512 bins", 512)
        self.bin_combo.addItem("1024 bins", 1024)
        self.bin_combo.setToolTip("Target number of histogram bins.\n'Auto' dynamically scales based on panel width.")
        self.bin_combo.setCurrentIndex(self.bin_combo.findData(self.settings.global_.bin_res))
        self.bin_combo.currentIndexChanged.connect(self.on_bin_resolution_changed)
        self.controls_layout.addWidget(self.bin_combo)

        self.factor_label = QLabel("Clamp factor", self)
        self.controls_layout.addWidget(self.factor_label)

        self.factor_spin = QDoubleSpinBox(
            self,
            suffix=" %",
            decimals=3,
            minimum=0.001,
            maximum=100.0,
            singleStep=0.001,
            value=self.settings.global_.factor,
        )
        self.factor_spin.setToolTip(
            "Clamping threshold for peak pixel counts\nto make smaller peaks visible (0.001% to 100%)"
        )
        self.factor_spin.valueChanged.connect(self.on_factor_changed)
        self.controls_layout.addWidget(self.factor_spin)

        self.unsafe_checkbox = QCheckBox("Show unsafe zones", self)
        self.unsafe_checkbox.setChecked(self.settings.global_.show_unsafe)
        self.unsafe_checkbox.setToolTip("Highlight broadcast unsafe ranges in YUV format.")
        self.unsafe_checkbox.stateChanged.connect(self.on_show_unsafe_zones_changed)
        self.controls_layout.addWidget(self.unsafe_checkbox)
        self.controls_layout.addStretch()

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addLayout(self.controls_layout)
        self.main_layout.addWidget(self.container)

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

    @run_in_loop(return_future=False)
    def update_histogram(self, n: int | None = None, voutput: VideoOutputProxy | None = None) -> None:
        if self.api.is_playing:
            return

        n = fallback(n, self.api.current_frame)
        voutput = fallback(voutput, self.api.current_voutput)

        self.container.update_voutput(voutput)

        with self.api.vs_context(), voutput.vs_output.clip.get_frame(n) as frame:
            self.container.update_histogram(frame)

    def on_bin_resolution_changed(self, index: int) -> None:
        self.update_global_settings(bin_res=self.bin_combo.currentData())
        self.update_histogram()

    def on_factor_changed(self, value: float) -> None:
        self.update_global_settings(factor=value)
        self.update_histogram()

    def on_show_unsafe_zones_changed(self, state: int) -> None:
        self.update_global_settings(show_unsafe=self.unsafe_checkbox.isChecked())
        self.update_histogram()
