from __future__ import annotations

import enum
from functools import cache
from logging import getLogger
from typing import Literal, NamedTuple, Self, override

import numpy as np
import vapoursynth as vs
from jetpytools import cachedproperty
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPainter, QPainterPath, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from vsview.api import PluginAPI, PluginSettings

from ..settings import GlobalSettings
from ..utils import CustomContextMenu, write_to_qimage

logger = getLogger(__name__)

MAX_VAL_X = 0.85
MAX_VAL_Y = 0.85

SPECTRAL_LOCUS_XY = np.array(
    [
        [0.1741, 0.0050],  # 380nm
        [0.1740, 0.0050],  # 385nm
        [0.1738, 0.0049],  # 390nm
        [0.1736, 0.0049],  # 395nm
        [0.1733, 0.0048],  # 400nm
        [0.1730, 0.0048],  # 405nm
        [0.1726, 0.0048],  # 410nm
        [0.1721, 0.0048],  # 415nm
        [0.1714, 0.0051],  # 420nm
        [0.1703, 0.0058],  # 425nm
        [0.1689, 0.0069],  # 430nm
        [0.1669, 0.0086],  # 435nm
        [0.1644, 0.0109],  # 440nm
        [0.1611, 0.0138],  # 445nm
        [0.1566, 0.0177],  # 450nm
        [0.1510, 0.0227],  # 455nm
        [0.1440, 0.0297],  # 460nm
        [0.1355, 0.0399],  # 465nm
        [0.1241, 0.0578],  # 470nm
        [0.1096, 0.0868],  # 475nm
        [0.0913, 0.1327],  # 480nm
        [0.0687, 0.2007],  # 485nm
        [0.0454, 0.2950],  # 490nm
        [0.0235, 0.4127],  # 495nm
        [0.0082, 0.5384],  # 500nm
        [0.0039, 0.6548],  # 505nm
        [0.0139, 0.7502],  # 510nm
        [0.0389, 0.8120],  # 515nm
        [0.0743, 0.8338],  # 520nm
        [0.1142, 0.8262],  # 525nm
        [0.1547, 0.8059],  # 530nm
        [0.1929, 0.7816],  # 535nm
        [0.2296, 0.7543],  # 540nm
        [0.2658, 0.7243],  # 545nm
        [0.3016, 0.6923],  # 550nm
        [0.3374, 0.6588],  # 555nm
        [0.3731, 0.6245],  # 560nm
        [0.4087, 0.5896],  # 565nm
        [0.4441, 0.5547],  # 570nm
        [0.4788, 0.5202],  # 575nm
        [0.5125, 0.4866],  # 580nm
        [0.5448, 0.4544],  # 585nm
        [0.5752, 0.4242],  # 590nm
        [0.6029, 0.3965],  # 595nm
        [0.6270, 0.3725],  # 600nm
        [0.6482, 0.3514],  # 605nm
        [0.6658, 0.3340],  # 610nm
        [0.6801, 0.3197],  # 615nm
        [0.6915, 0.3083],  # 620nm
        [0.7006, 0.2993],  # 625nm
        [0.7079, 0.2920],  # 630nm
        [0.7140, 0.2859],  # 635nm
        [0.7190, 0.2809],  # 640nm
        [0.7230, 0.2769],  # 645nm
        [0.7260, 0.2740],  # 650nm
        [0.7283, 0.2717],  # 655nm
        [0.7300, 0.2700],  # 660nm
        [0.7311, 0.2689],  # 665nm
        [0.7320, 0.2680],  # 670nm
        [0.7327, 0.2673],  # 675nm
        [0.7334, 0.2666],  # 680nm
        [0.7340, 0.2660],  # 685nm
        [0.7344, 0.2656],  # 690nm
        [0.7346, 0.2654],  # 695nm
        [0.7347, 0.2653],  # 700nm
    ],
    dtype=np.float32,
)
"""
Standard CIE 1931 2-degree Standard Observer xy chromaticity coordinates for the spectral locus boundary.
"""

XYZ_TO_SRGB_MAT = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float32,
)
"""
http://www.brucelindbloom.com/index.html?Eqn_RGB_XYZ_Matrix.html
"""


class Point(NamedTuple):
    x: float
    y: float


class GamutRGB(NamedTuple):
    r: Point
    g: Point
    b: Point


class GamutData(NamedTuple):
    rec709: GamutRGB
    rec601: GamutRGB
    dcip3: GamutRGB
    rec2020: GamutRGB
    d65: Point


class Gamut(enum.StrEnum):
    CIE_1931 = (
        "cie1931",
        GamutData(
            rec709=GamutRGB(Point(0.640, 0.330), Point(0.300, 0.600), Point(0.150, 0.060)),
            rec601=GamutRGB(Point(0.630, 0.340), Point(0.310, 0.595), Point(0.155, 0.070)),
            dcip3=GamutRGB(Point(0.680, 0.320), Point(0.2651, 0.690), Point(0.150, 0.060)),
            rec2020=GamutRGB(Point(0.708, 0.292), Point(0.170, 0.797), Point(0.131, 0.046)),
            d65=Point(0.3127, 0.3290),
        ),
    )
    """
    Gamut Primary Vertices in CIE 1931 xy.

    References:
    - Rec. 709 / sRGB: ITU-R Recommendation BT.709-6 (https://www.itu.int/rec/R-REC-BT.709/en)
    - Rec. 601 / SMPTE 170M: ITU-R Recommendation BT.601 (https://www.itu.int/rec/R-REC-BT.601/en)
    - DCI-P3: SMPTE RP 431-2 / Wikipedia definition (https://en.wikipedia.org/wiki/DCI-P3)
    - Rec. 2020: ITU-R Recommendation BT.2020-2 (https://www.itu.int/rec/R-REC-BT.2020/en)
    - Illuminant D65 (Standard Daylight): CIE 15:2004 (x=0.3127, y=0.3290)
    """

    CIE_1976 = (
        "cie1976",
        GamutData(
            rec709=GamutRGB(Point(0.4507, 0.5229), Point(0.1250, 0.5625), Point(0.1754, 0.1579)),
            rec601=GamutRGB(Point(0.4330, 0.5258), Point(0.1303, 0.5625), Point(0.1756, 0.1785)),
            dcip3=GamutRGB(Point(0.4964, 0.5255), Point(0.0991, 0.5794), Point(0.1754, 0.1579)),
            rec2020=GamutRGB(Point(0.5584, 0.5202), Point(0.0663, 0.5818), Point(0.1594, 0.1227)),
            d65=Point(0.1978, 0.4683),
        ),
    )
    """
    Gamut Primary Vertices in CIE 1976 u'v' Uniform Chromaticity Scale (UCS) space.
    Calculated from the 1931 xy coordinates using standard conversion.

    References:
    - https://en.wikipedia.org/wiki/CIE_1976_color_space
    - https://en.wikipedia.org/wiki/CIELUV#The_forward_transformation
    u' = 4x / (-2x + 12y + 3)
    v' = 9y / (-2x + 12y + 3)
    """

    REC_709 = "rec709", None, QColor(100, 255, 100), "Rec.709"
    REC_601 = "rec601", None, QColor(100, 180, 255), "Rec.601"
    DCI_P3 = "dcip3", None, QColor(255, 200, 50), "DCI-P3"
    REC_2020 = "rec2020", None, QColor(255, 100, 100), "Rec.2020"

    data: GamutData
    color: QColor
    label: str

    def __new__(
        cls,
        value: str,
        data: GamutData | None = None,
        color: QColor | None = None,
        label: str | None = None,
    ) -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        if data:
            obj.data = data
        if color:
            obj.color = color
        if label:
            obj.label = label
        return obj


class CIEDiagramWidget(QWidget):
    def __init__(self, parent: QWidget | None, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.api = api
        self.settings = settings

        self.scope_image = QImage(128, 128, QImage.Format.Format_RGBA8888)
        self.scope_image.fill(0)

        self.context_menu = CustomContextMenu(self, self.api)

        self._error_reason: str | None = None

    @cachedproperty
    def color_table(self) -> list[int]:
        """Neon cyan/blue phosphor color table"""
        colors = list[int]()
        for i in range(256):
            r = max(0, i - 192) * 4
            g = min(255, int(i * 1.1))
            b = min(255, int(i * 1.3))
            colors.append(QColor(r, g, b).rgba())
        return colors

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu.exec(event.globalPos())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        painter.fillRect(self.rect(), QColor(20, 20, 20))

        if self._error_reason:
            painter.setPen(QPen(QColor(220, 80, 80), 1))
            font = painter.font()
            font.setPointSize(20)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._error_reason)
            return

        # Draw square CIE diagram centering inside widget bounds
        side = min(self.width(), self.height())
        target_rect = QRect((self.width() - side) // 2, (self.height() - side) // 2, side, side)

        # Draw pre-rendered outline background horseshoe
        render_mode = self.settings.global_.cie.render_mode
        painter.drawImage(target_rect, get_cached_background(self.settings.global_.cie.mode, render_mode))

        if render_mode == "chroma_wheel":
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            painter.drawImage(target_rect, self.scope_image)
            painter.restore()
        else:
            painter.drawImage(target_rect, self.scope_image)

        # Draw gamut triangles and labels
        self.draw_graticules(painter, target_rect)

    def draw_graticules(self, painter: QPainter, rect: QRect) -> None:
        painter.save()
        painter.translate(rect.left(), rect.top())
        painter.scale(rect.width() / 1024.0, rect.height() / 1024.0)

        gamuts = Gamut(self.settings.global_.cie.mode).data

        font = painter.font()
        font.setPointSize(16)
        painter.setFont(font)

        # Draw Axes/Grid Border
        painter.setPen(QPen(QColor(120, 120, 120), 2))
        painter.drawRect(0, 0, 1024, 1024)

        # Grid coordinates labels
        painter.setPen(QColor(150, 150, 150))
        for val in (val for val in [0.2, 0.4, 0.6, 0.8] if val < MAX_VAL_X):
            pos_x = int((val / MAX_VAL_X) * 1024.0)
            painter.drawLine(pos_x, 1016, pos_x, 1024)
            painter.drawText(pos_x - 20, 1004, f"{val:.1f}")
        for val in (val for val in [0.2, 0.4, 0.6, 0.8] if val < MAX_VAL_Y):
            pos_y = int((1.0 - (val / MAX_VAL_Y)) * 1024.0)
            painter.drawLine(0, pos_y, 8, pos_y)
            painter.drawText(16, pos_y + 8, f"{val:.1f}")

        # Gamuts to draw
        settings = self.settings.global_.cie
        to_draw = list[Gamut]()
        if settings.show_rec709:
            to_draw.append(Gamut.REC_709)
        if settings.show_rec601:
            to_draw.append(Gamut.REC_601)
        if settings.show_dcip3:
            to_draw.append(Gamut.DCI_P3)
        if settings.show_rec2020:
            to_draw.append(Gamut.REC_2020)

        # Draw all gamut polygons first
        for draw in to_draw:
            pts: GamutRGB = getattr(gamuts, draw.value)
            poly = QPolygonF()
            # Map points (x, y) -> (x / MAX_VAL_X * 1024, (1 - y / MAX_VAL_Y) * 1024)
            poly.append(QPointF((pts.r.x / MAX_VAL_X) * 1024.0, (1.0 - (pts.r.y / MAX_VAL_Y)) * 1024.0))
            poly.append(QPointF((pts.g.x / MAX_VAL_X) * 1024.0, (1.0 - (pts.g.y / MAX_VAL_Y)) * 1024.0))
            poly.append(QPointF((pts.b.x / MAX_VAL_X) * 1024.0, (1.0 - (pts.b.y / MAX_VAL_Y)) * 1024.0))

            painter.setPen(QPen(draw.color, 1.5, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(poly)

        # Draw D65 White Point crosshair
        w_pt = QPointF((gamuts.d65.x / MAX_VAL_X) * 1024.0, (1.0 - (gamuts.d65.y / MAX_VAL_Y)) * 1024.0)
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        painter.drawLine(int(w_pt.x()) - 10, int(w_pt.y()), int(w_pt.x()) + 10, int(w_pt.y()))
        painter.drawLine(int(w_pt.x()), int(w_pt.y()) - 10, int(w_pt.x()), int(w_pt.y()) + 10)

        # Draw all labels last so they are above all lines/polygons
        for draw in to_draw:
            pts: GamutRGB = getattr(gamuts, draw.value)  # type: ignore[no-redef]
            # Draw labels near green primary vertices
            painter.setPen(draw.color)
            painter.drawText(
                int((pts.g.x / MAX_VAL_X) * 1024.0) - 30, int((1.0 - (pts.g.y / MAX_VAL_Y)) * 1024.0) - 16, draw.label
            )

        # Draw D65 label
        painter.setPen(QColor(255, 255, 255, 200))
        painter.drawText(int(w_pt.x()) + 12, int(w_pt.y()) + 8, "D65")

        painter.restore()

    def update_frame(self, linear_frame: vs.VideoFrame, xyz_frame: vs.VideoFrame) -> None:
        self._error_reason = None

        size = xyz_frame.height if (res := self.settings.global_.cie.res) == 0 else res
        xyz = np.asarray(xyz_frame)

        # Apply safety stride for very large resolutions (e.g. 4K 4:4:4)
        _, h, w = xyz.shape
        stride = max(1, int(np.sqrt(total_elements / 2_000_000))) if (total_elements := h * w) > 2_000_000 else 1
        xyz_sliced = xyz[:, ::stride, ::stride].reshape(3, -1)

        # Calculate target coordinates
        x, y, z = xyz_sliced
        if self.settings.global_.cie.mode == "cie1976":
            denom = (x + 15.0 * y + 3.0 * z).clip(1e-6, None)
            x_coord = 4.0 * x / denom
            y_coord = 9.0 * y / denom
        else:  # cie1931
            denom = (x + y + z).clip(1e-6, None)
            x_coord = x / denom
            y_coord = y / denom

        # Map to size-based pixel grid coordinates
        x_pixel = ((x_coord / MAX_VAL_X) * size).clip(0.0, size - 1.0).astype(np.int32)
        y_pixel = ((1.0 - (y_coord / MAX_VAL_Y)) * size).clip(0.0, size - 1.0).astype(np.int32)
        indices = y_pixel * size + x_pixel

        # Generate 2D color cloud density counts
        counts = np.bincount(indices, minlength=(size * size))
        counts_2d = counts.reshape((size, size))

        render_mode = self.settings.global_.cie.render_mode

        if render_mode == "pixel_color":
            # Extract and normalize original colors in linear space
            lrgb = np.asarray(linear_frame)
            lrgb_sliced = lrgb[:, ::stride, ::stride].reshape(3, -1)
            lrgb_sliced_clipped = lrgb_sliced.clip(0.0, 1.0)

            # Accumulate linear R, G, B colors of the pixels falling into each bin
            # Multiply by 255.0 to get standard range values
            rgb_sum = np.stack(
                [np.bincount(indices, weights=p * 255.0, minlength=(size * size)) for p in lrgb_sliced_clipped]
            )
            rgb_grid = rgb_sum.reshape((3, size, size))

            # Normalize to [0, 1], apply Gamma 2.2 correction on the size^2 canvas,
            # and scale to 255.0 (counts cancel out during division)
            max_channel = rgb_grid.max(axis=0)
            max_channel_safe = np.where(max_channel == 0.0, 1.0, max_channel)
            rgb_norm_color = ((rgb_grid / max_channel_safe) ** (1 / 2.2)) * 255.0

            # Scale pixel brightness by density log-scale
            if (max_count := counts_2d.max()) > 0:
                # density_scale is in range [0.3, 1.0] for populated bins to ensure visibility
                density_scale = (0.3 + 0.7 * (np.log1p(counts_2d) / np.log1p(max_count))) * (counts_2d > 0)

                rgb_final = (
                    (rgb_norm_color * density_scale * self.settings.global_.cie.luma).clip(0, 255).astype(np.uint8)
                )
                alpha = (counts_2d > 0).astype(np.uint8) * np.uint8(255)

                rgba = np.empty((size, size, 4), dtype=np.uint8)
                rgba[..., :3] = rgb_final.transpose(1, 2, 0)
                rgba[..., 3] = alpha
            else:
                rgba = np.zeros((size, size, 4), dtype=np.uint8)

            self.scope_image = write_to_qimage(self.scope_image, rgba, QImage.Format.Format_RGBA8888)

        elif render_mode == "density":
            if (max_count := counts_2d.max()) > 0:
                scale = 255.0 / np.log1p(max_count)
                grid_img = (np.log1p(counts_2d) * scale).astype(np.uint8)
            else:
                grid_img = counts_2d.astype(np.uint8)

            self.scope_image = write_to_qimage(
                self.scope_image,
                grid_img,
                QImage.Format.Format_Indexed8,
                self.color_table,
            )

        elif render_mode == "chroma_wheel":
            if (max_count := counts_2d.max()) > 0:
                scale = 255.0 / np.log1p(max_count)
                density_val = np.log1p(counts_2d) * scale
                density_val = (density_val * self.settings.global_.cie.luma).clip(0, 255).astype(np.uint8)

                rgba = np.empty((size, size, 4), dtype=np.uint8)
                rgba[..., :3] = 255  # White glow
                rgba[..., 3] = density_val
            else:
                rgba = np.zeros((size, size, 4), dtype=np.uint8)

            self.scope_image = write_to_qimage(self.scope_image, rgba, QImage.Format.Format_RGBA8888)

        self.update()

    def paint_error(self, message: str) -> None:
        self._error_reason = message
        logger.warning(message)
        self.update()


class CIEDiagramContainerWidget(QFrame):
    def __init__(self, parent: QWidget, api: PluginAPI, settings: PluginSettings[GlobalSettings, None]) -> None:
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)

        self.current_layout = QVBoxLayout(self)
        self.current_layout.setContentsMargins(0, 0, 0, 0)

        self.cie_diagram = CIEDiagramWidget(self, api, settings)
        self.current_layout.addWidget(self.cie_diagram)

    def update_histogram(self, linear_frame: vs.VideoFrame, xyz_frame: vs.VideoFrame) -> None:
        self.cie_diagram.update_frame(linear_frame, xyz_frame)


@cache
def get_cached_background(
    mode: Literal["cie1931", "cie1976"],
    render_mode: Literal["density", "chroma_wheel", "pixel_color"],
    size: int = 1024,
) -> QImage:
    points = list[QPointF]()
    if mode == "cie1976":
        for x, y in SPECTRAL_LOCUS_XY:
            denom_l = -2.0 * x + 12.0 * y + 3.0
            u = 4.0 * x / denom_l
            v = 9.0 * y / denom_l
            points.append(QPointF((u / MAX_VAL_X) * size, (1.0 - (v / MAX_VAL_Y)) * size))
    else:
        for x, y in SPECTRAL_LOCUS_XY:
            points.append(QPointF((x / MAX_VAL_X) * size, (1.0 - (y / MAX_VAL_Y)) * size))

    bg_img = QImage(size, size, QImage.Format.Format_ARGB32)
    bg_img.fill(Qt.GlobalColor.transparent)

    with QPainter(bg_img) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        poly = QPolygonF(points)

        # Draw colored background inside the polygon if chroma_wheel mode is selected
        if render_mode == "chroma_wheel":
            col = np.arange(size, dtype=np.float32)
            row = np.arange(size, dtype=np.float32)

            if mode == "cie1976":
                u_coord, v_coord = np.meshgrid((col / size) * MAX_VAL_X, (1.0 - (row / size)) * MAX_VAL_Y)
                denom_uv = 6.0 * u_coord - 16.0 * v_coord + 12.0
                denom_uv = np.where(np.abs(denom_uv) < 1e-6, 1e-6, denom_uv)
                x_coord = 9.0 * u_coord / denom_uv
                y_coord = 4.0 * v_coord / denom_uv
            else:
                x_coord, y_coord = np.meshgrid((col / size) * MAX_VAL_X, (1.0 - (row / size)) * MAX_VAL_Y)

            denom = np.maximum(y_coord, 1e-6)
            x = x_coord / denom
            y = np.ones_like(x)
            z = (1.0 - x_coord - y_coord) / denom

            xyz = np.dstack([x, y, z])
            rgb_linear = xyz @ XYZ_TO_SRGB_MAT.T
            # Darken the background slightly (scale by 0.25) to make the density cloud pop
            rgb_srgb = np.clip(rgb_linear * 0.25, 0.0, 1.0) ** (1.0 / 2.2)

            rgba = np.empty((size, size, 4), dtype=np.uint8)
            rgba[..., :3] = (rgb_srgb * 255.0).astype(np.uint8)
            rgba[..., 3] = 255

            fmt = QImage.Format.Format_RGBX8888
            grad_img = write_to_qimage(QImage(size, size, fmt), rgba, fmt)

            painter.save()
            path = QPainterPath()
            path.addPolygon(poly)
            painter.setClipPath(path)
            painter.drawImage(0, 0, grad_img)
            painter.restore()

        # Thin gray boundary outline
        painter.setPen(QPen(QColor(200, 200, 200, 255), 1.5))
        painter.drawPolygon(poly)

    return bg_img
