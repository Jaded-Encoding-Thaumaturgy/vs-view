from enum import StrEnum
from typing import Annotated, override

from pydantic import BaseModel, ConfigDict, PlainSerializer, PlainValidator
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyle

ThemeColor = Annotated[
    QColor,
    PlainValidator(lambda v: v if isinstance(v, QColor) else QColor(v)),
    PlainSerializer(lambda color: color.name(), return_type=str),
]


class ColorScheme(StrEnum):
    DARK = "dark"
    LIGHT = "light"


class ThemePalette(BaseModel):
    model_config = ConfigDict(frozen=True)

    primary: ThemeColor
    secondary: ThemeColor

    base: ThemeColor
    mantle: ThemeColor
    crust: ThemeColor

    surface0: ThemeColor
    surface1: ThemeColor
    surface2: ThemeColor

    overlay0: ThemeColor
    overlay1: ThemeColor
    overlay2: ThemeColor

    text: ThemeColor


class Theme(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    scheme: ColorScheme
    palette: ThemePalette
    is_custom: bool = False

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def is_dark(self) -> bool:
        return self.scheme == ColorScheme.DARK

    def create_palette(self, base_style: QStyle | None = None) -> QPalette:
        palette = base_style.standardPalette() if base_style else QPalette()
        self.apply_to_palette(palette)
        return palette

    def apply_to_palette(self, palette: QPalette) -> None:
        p = self.palette
        highlighted_color = p.primary
        highlighted_text_color = p.mantle if highlighted_color.valueF() > 0.5 else p.text

        bright_text_color = QColor.fromHsvF(
            p.text.hueF(),
            p.text.saturationF(),
            1.0 - p.text.valueF(),
            p.text.alphaF(),
        )

        cg = QPalette.ColorGroup
        cr = QPalette.ColorRole

        # Normal
        if self.is_dark:
            palette.setColor(cr.Base, p.mantle)
            palette.setColor(cr.AlternateBase, p.base)
        else:
            palette.setColor(cr.Base, p.crust)
            palette.setColor(cr.AlternateBase, p.mantle)

        palette.setColor(cr.Window, p.base)
        palette.setColor(cr.WindowText, p.text)
        palette.setColor(cr.PlaceholderText, p.overlay1)
        palette.setColor(cr.Text, p.text)
        palette.setColor(cr.Button, p.base)
        palette.setColor(cr.ButtonText, p.text)
        palette.setColor(cr.BrightText, bright_text_color)
        palette.setColor(cr.ToolTipBase, p.mantle)
        palette.setColor(cr.ToolTipText, p.overlay2)

        palette.setColor(cr.Highlight, highlighted_color)
        palette.setColor(cr.HighlightedText, highlighted_text_color)
        palette.setColor(cr.Link, p.secondary)
        palette.setColor(cr.LinkVisited, p.secondary)

        palette.setColor(cr.Light, p.crust)
        palette.setColor(cr.Midlight, p.mantle)
        palette.setColor(cr.Mid, p.surface0)
        palette.setColor(cr.Dark, p.surface1)
        palette.setColor(cr.Shadow, p.overlay0)

        # Inactive
        palette.setColor(cg.Inactive, cr.Highlight, p.surface1)
        palette.setColor(cg.Inactive, cr.Link, p.surface1)
        palette.setColor(cg.Inactive, cr.LinkVisited, p.surface1)

        # Disabled
        palette.setColor(cg.Disabled, cr.WindowText, p.overlay1)
        palette.setColor(cg.Disabled, cr.Base, p.base)
        palette.setColor(cg.Disabled, cr.AlternateBase, p.base)
        palette.setColor(cg.Disabled, cr.Text, p.overlay1)
        palette.setColor(cg.Disabled, cr.PlaceholderText, p.overlay1)
        palette.setColor(cg.Disabled, cr.Button, p.base)
        palette.setColor(cg.Disabled, cr.ButtonText, p.overlay1)
        palette.setColor(cg.Disabled, cr.BrightText, p.mantle)

        palette.setColor(cg.Disabled, cr.Highlight, p.surface2)
        palette.setColor(cg.Disabled, cr.HighlightedText, p.surface0)
        palette.setColor(cg.Disabled, cr.Link, p.surface0)
        palette.setColor(cg.Disabled, cr.LinkVisited, p.surface0)

        # Accent
        palette.setColor(cr.Accent, p.secondary)
        palette.setColor(cg.Inactive, cr.Accent, p.surface1)
        palette.setColor(cg.Disabled, cr.Accent, p.surface2)
