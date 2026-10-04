from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from logging import getLogger
from typing import override

from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget
from vstools import Range, get_y, vs

from vsview.api import PluginAPI, PluginGraphicsView, PluginSettings

from ..funcs import compute_luma
from ..settings import GlobalSettings

logger = getLogger(__name__)


def set_props_on_output[**P](function: Callable[P, vs.VideoNode]) -> Callable[P, vs.VideoNode]:
    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> vs.VideoNode:
        return function(*args, **kwargs).std.SetFrameProps(
            _Matrix=vs.MATRIX_BT709,
            _Primaries=vs.PRIMARIES_BT709,
            _Transfer=vs.TRANSFER_BT709,
        )

    return wrapper


class LumaView(PluginGraphicsView):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent, api)
        self.settings = settings

    @override
    @set_props_on_output
    def get_node(self, clip: vs.VideoNode) -> vs.VideoNode:
        if (cfam := clip.format.color_family) not in (vs.GRAY, vs.YUV):
            logger.warning("%s input - no luma data", cfam.name)
            return clip.std.BlankClip(format=vs.GRAY8, keep=True).text.Text(f"{cfam.name} input - no luma data", 5, 4)

        bits = clip.format.bits_per_sample
        shift_in = self.settings.global_.luma.shift
        use_sawtooth = self.settings.global_.luma.sawtooth
        sample_type = clip.format.sample_type

        def modify_frame_func(n: int, f: list[vs.VideoFrame]) -> vs.VideoFrame:
            src_f = f[1]
            frame_dst = f[0].copy()
            is_limited = Range.from_video(src_f).is_limited

            src_ptr = src_f.get_read_ptr(0).value or 0
            dst_ptr = frame_dst.get_write_ptr(0).value or 0

            compute_luma(
                src=(src_ptr, src_f.get_stride(0)),
                dst=(dst_ptr, frame_dst.get_stride(0)),
                width=src_f.width,
                height=src_f.height,
                bits=bits,
                sample_type=sample_type,
                shift_in=shift_in,
                use_sawtooth=use_sawtooth,
                is_limited=is_limited,
            )

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
