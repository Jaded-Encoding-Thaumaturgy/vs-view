from PySide6.QtGui import QPalette

from .bridge import ThemeTokens


def extract_theme_tokens(palette: QPalette) -> ThemeTokens:
    window_color = palette.color(QPalette.ColorRole.Window)
    is_dark = window_color.lightnessF() < 0.5

    base_color = palette.color(QPalette.ColorRole.Base)
    text_color = palette.color(QPalette.ColorRole.WindowText)
    highlight_color = palette.color(QPalette.ColorRole.Highlight)

    if is_dark:
        surface = base_color if base_color.name() != window_color.name() else window_color.lighter(115)
        surface_elevated = surface.lighter(115)
        surface_hover = surface.lighter(130)
        border = window_color.lighter(210)
        text_secondary = palette.color(QPalette.ColorRole.PlaceholderText)
        if text_secondary.alpha() == 0 or text_secondary.name() == text_color.name():
            text_secondary = text_color.darker(140)
        text_muted = text_color.darker(190)
    else:
        surface = base_color if base_color.name() != window_color.name() else window_color.darker(105)
        surface_elevated = surface.darker(108)
        surface_hover = surface.darker(115)
        border = window_color.darker(120)
        text_secondary = palette.color(QPalette.ColorRole.PlaceholderText)
        if text_secondary.alpha() == 0 or text_secondary.name() == text_color.name():
            text_secondary = text_color.lighter(140)
        text_muted = text_color.lighter(190)

    return {
        "isDark": is_dark,
        "bg": window_color.name(),
        "surface": surface.name(),
        "surfaceElevated": surface_elevated.name(),
        "surfaceHover": surface_hover.name(),
        "border": border.name(),
        "textPrimary": text_color.name(),
        "textSecondary": text_secondary.name(),
        "textMuted": text_muted.name(),
        "accent": highlight_color.name() if highlight_color.isValid() else "#38bdf8",
        "accentAmber": "#f59e0b",
        "accentEmerald": "#10b981",
        # Qt has no error role in QPalette, so alerts use a fixed readable red.
        "danger": "#ef4444",
    }
