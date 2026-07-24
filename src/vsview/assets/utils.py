"""Asset utilities for vsview."""

from functools import cache
from importlib import resources
from logging import getLogger
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

_logger = getLogger(__name__)


def load_svg(svg_data: bytes, size: QSize, color: QColor | None = None, dpr: float | None = None) -> QPixmap:
    if dpr is None:
        app = QApplication.instance()
        dpr = app.devicePixelRatio() if isinstance(app, QApplication) else 1.0

    renderer = QSvgRenderer(svg_data)
    pixmap = QPixmap(size * dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        renderer.render(painter)

        if color is not None:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QColor(color))

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


@cache
def load_fonts() -> None:
    """Load bundled fonts into Qt's font database."""
    fonts = resources.files("vsview.assets.fonts")

    for font_file in [
        "Cascadia_Mono/CascadiaMono-VariableFont_wght.ttf",
        "Cascadia_Mono/CascadiaMono-Italic-VariableFont_wght.ttf",
    ]:
        try:
            font_data = fonts.joinpath(font_file).read_bytes()
            font_id = QFontDatabase.addApplicationFontFromData(font_data)

            if font_id < 0:
                _logger.warning("Failed to load font: %s", font_file)
            else:
                _logger.debug(
                    "Loaded font %s: %r", font_file, lambda fid=font_id: QFontDatabase.applicationFontFamilies(fid)
                )
        except Exception as e:
            _logger.warning("Error loading font %s: %s", font_file, e)
            _logger.debug("Full traceback", exc_info=True)


@cache
def get_monospace_font(size: int | None = None) -> QFont:
    """
    Get the preferred monospace font.

    Returns Cascadia Mono if available (after load_fonts() is called), otherwise falls back to system monospace font.

    Args:
        size: Optional point size for the font.

    Returns:
        A QFont configured for monospace display.
    """
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setStyleStrategy(QFont.StyleStrategy.PreferQuality | QFont.StyleStrategy.PreferAntialias)
    font.setWeight(QFont.Weight.DemiBold)

    if size is not None:
        font.setPointSize(size)

    return font


@cache
def app_icon() -> QIcon:
    return QIcon(str(Path(__file__).parent / "icon.png"))


@cache
def loading_icon() -> QIcon:
    return QIcon(str(Path(__file__).parent / "loading.png"))
