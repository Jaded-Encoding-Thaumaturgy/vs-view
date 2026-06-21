from __future__ import annotations

import ctypes
from logging import getLogger
from typing import override

import numpy as np
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from vstools import Range, get_y, vs

from vsview.api import PluginAPI, PluginGraphicsView, PluginSettings

from ..settings import GlobalSettings

logger = getLogger(__name__)


class LumaView(PluginGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent, api)
        self.settings = settings
        self.numba_ready = False

    @override
    def on_current_frame_changed(self, n: int, f: vs.VideoFrame) -> None:
        if self.numba_ready:
            super().on_current_frame_changed(n, f)

    @override
    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        if (cfam := clip.format.color_family) not in (vs.GRAY, vs.YUV):
            logger.warning("%s input - no luma data", cfam.name)
            return clip.std.BlankClip(format=vs.GRAY8).text.Text(f"{cfam.name} input - no luma data", 5, 4)

        bits = clip.format.bits_per_sample
        shift_in = self.settings.global_.luma.shift
        use_sawtooth = self.settings.global_.luma.sawtooth
        sample_type = clip.format.sample_type
        fp16 = (sample_type, bits) == (vs.FLOAT, 16)

        from .numba_backend import process_luma_numba

        def modify_frame_func(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
            arr = np.asarray(f[1][0], np.float32 if fp16 else None)
            is_limited = Range.from_video(f[1]).is_limited

            frame_dst = f[0].copy()
            dst_ptr = ctypes.cast(frame_dst.get_write_ptr(0), ctypes.POINTER(ctypes.c_uint8))
            dst_arr = np.ctypeslib.as_array(dst_ptr, shape=(frame_dst.height, frame_dst.get_stride(0)))

            process_luma_numba(arr, dst_arr, bits, shift_in, use_sawtooth, is_limited)

            return frame_dst

        blank = clip.std.BlankClip(format=vs.GRAY8, keep=True)

        return blank.std.ModifyFrame(clips=[blank, get_y(clip)], selector=modify_frame_func)


class LumaContainerWidget(QFrame):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)

        self.current_layout = QHBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)

        self.view = LumaView(self, self.api, self.settings)
        self.current_layout.addWidget(self.view)
