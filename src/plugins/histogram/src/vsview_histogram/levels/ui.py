from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import vapoursynth as vs
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget
from vstools import Range, get_lowest_value, get_peak_value

from vsview.api import PluginSettings, VideoOutputProxy

if TYPE_CHECKING:
    from .charts import LevelsChartView

from ..settings import GlobalSettings


class HistogramContainerWidget(QFrame):
    def __init__(self, parent: QWidget, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)

        self.settings = settings

        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self.current_layout = QVBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)
        self.current_layout.setSpacing(8)

        self.charts: list[LevelsChartView] = []

    def update_voutput(self, voutput: VideoOutputProxy) -> None:
        self._fmt = voutput.vs_output.clip.format._as_dict()
        # FIXME: _as_dict doesn't pass num_planes in R77
        self._fmt["num_planes"] = voutput.vs_output.clip.format.num_planes

        self.setup_layout(self._fmt["num_planes"])

        for i in range(self._fmt["num_planes"]):
            self.charts[i].configure(self._fmt, i, self.settings.global_.show_unsafe)

    def setup_layout(self, num_planes: int) -> None:
        from .charts import LevelsChartView

        # Only create charts if we don't have enough
        while len(self.charts) < num_planes:
            chart = LevelsChartView(self)
            self.charts.append(chart)
            self.current_layout.addWidget(chart, stretch=1)

        for i in range(num_planes):
            self.charts[i].show()
        for i in range(num_planes, len(self.charts)):
            self.charts[i].hide()

    def update_histogram(self, frame: vs.VideoFrame) -> None:
        bin_res = self.settings.global_.bin_res

        for plane in range(frame.format.num_planes):
            hist = compute_histogram(frame, plane, self.settings.global_.factor)
            chart = self.charts[plane]
            chart.update_data(hist, self.width(), bin_res)

    def update_settings(self) -> None:
        self.setup_layout(self._fmt["num_planes"])

        for i in range(self._fmt["num_planes"]):
            self.charts[i].configure(self._fmt, i, self.settings.global_.show_unsafe)


def compute_histogram(frame: vs.VideoFrame, plane: int, clamp_factor: float) -> npt.NDArray[np.intp]:
    arr = np.asarray(frame[plane])

    # Float format is digitized to 10-bit (1024 bins)
    if frame.format.sample_type is vs.FLOAT:
        bins_count = 1024
        data_int = scale_array_float(arr.astype(np.float32), frame, frame.format.color_family is vs.YUV and plane > 0)
    else:
        bins_count = 1 << frame.format.bits_per_sample
        data_int = arr.astype(np.int32).clip(0, bins_count - 1)

    hist = np.bincount(data_int.ravel(), minlength=bins_count)

    return hist.clip(0, max(1, int(arr.size * clamp_factor / 100.0))) if clamp_factor < 100.0 else hist


def scale_array_float(arr: npt.NDArray[np.float32], frame: vs.VideoFrame, chroma: bool) -> npt.NDArray[np.int32]:
    color_range = Range.from_video(frame)
    output_peak = get_peak_value(10, chroma, color_range, frame.format.color_family)
    output_lowest = get_lowest_value(10, chroma, color_range, frame.format.color_family)

    arr *= output_peak - output_lowest

    if chroma:
        arr += 128 << 2
    elif color_range.is_limited:
        arr += 16 << 2

    return arr.round().clip(0, 1023).astype(np.int32)
