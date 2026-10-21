---
icon: lucide/brush
title: Appearance
description: Configure color schemes, widget styles, palette themes, custom stylesheets, and icons.
---

# Appearance

Appearance settings configure the application's theme, widget style, icon set, and custom styling.

## Settings Dialog

Open the appearance settings from the menu bar:

- **Settings** > **Appearance** (or press <kbd>Ctrl</kbd> + <kbd>,</kbd> / <kbd>Cmd</kbd> + <kbd>,</kbd>)

Settings are saved to `global_settings.json`.

<figure markdown="span">
    ![](../assets/appearance_settings.png){ .lightboxOn }
</figure>

---

## Options

### Color Scheme

Sets the OS-level color scheme.

-  **System Default**: Follows the operating system theme. 
-  **Light**: Forces light mode for window decorations and menus.
-  **Dark**: Forces dark mode for window decorations and menus.

Selecting **Light** or **Dark** filters the **Palette Theme** dropdown to matching themes.

---

### Widget Style

Sets the Qt widget style (e.g., `Fusion`, `Windows`).

!!! tip "Recommended Style"

    It is recommended to use the **Fusion** widget style when a custom widget style is selected,
    as it provides the most consistent appearance across all controls.

---

### Palette Theme

Applies a color palette across the UI. The examples below use the Fusion widget style.

=== "Stock (Default)"

    <figure markdown="span">
        ![](../assets/palette_theme/stock_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/stock_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Adwaita"

    <figure markdown="span">
        ![](../assets/palette_theme/adwaita_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/adwaita_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Atom One Dark"

    <figure markdown="span">
        ![](../assets/palette_theme/atom_one.png){ .lightboxOn }
    </figure>

=== "Ayu"

    <figure markdown="span">
        ![](../assets/palette_theme/ayu_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/ayu_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Ayu Mirage"

    <figure markdown="span">
        ![](../assets/palette_theme/ayu_mirage.png){ .lightboxOn }
    </figure>

=== "Blender Dark"

    <figure markdown="span">
        ![](../assets/palette_theme/blender.png){ .lightboxOn }
    </figure>

=== "Breeze"

    <figure markdown="span">
        ![](../assets/palette_theme/breeze_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/breeze_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Catppuccin Frappé"

    <figure markdown="span">
        ![](../assets/palette_theme/catppuccin_frappe.png){ .lightboxOn }
    </figure>

=== "Catppuccin Macchiato"

    <figure markdown="span">
        ![](../assets/palette_theme/catppuccin_macchiato.png){ .lightboxOn }
    </figure>

=== "Catppuccin Mocha"

    <figure markdown="span">
        ![](../assets/palette_theme/catppuccin_latte.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/catppuccin_mocha.png#only-dark){ .lightboxOn }
    </figure>

=== "Dracula"

    <figure markdown="span">
        ![](../assets/palette_theme/dracula.png){ .lightboxOn }
    </figure>

=== "Everforest"

    <figure markdown="span">
        ![](../assets/palette_theme/everforest_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/everforest_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "GitHub"

    <figure markdown="span">
        ![](../assets/palette_theme/github_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/github_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Gruvbox"

    <figure markdown="span">
        ![](../assets/palette_theme/gruvbox_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/gruvbox_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Kanagawa"

    <figure markdown="span">
        ![](../assets/palette_theme/kanagawa_lotus.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/kanagawa_wave.png#only-dark){ .lightboxOn }
    </figure>

=== "Kanagawa Dragon"

    <figure markdown="span">
        ![](../assets/palette_theme/kanagawa_dragon.png){ .lightboxOn }
    </figure>

=== "Modern"

    <figure markdown="span">
        ![](../assets/palette_theme/modern_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/modern_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Monokai"

    <figure markdown="span">
        ![](../assets/palette_theme/monokai.png){ .lightboxOn }
    </figure>

=== "Nord"

    <figure markdown="span">
        ![](../assets/palette_theme/nord.png){ .lightboxOn }
    </figure>

=== "One Dark Two"

    <figure markdown="span">
        ![](../assets/palette_theme/one_dark_two.png){ .lightboxOn }
    </figure>

=== "Rosé Pine"

    <figure markdown="span">
        ![](../assets/palette_theme/rose_pine_dawn.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/rose_pine.png#only-dark){ .lightboxOn }
    </figure>

=== "Rosé Pine Moon"

    <figure markdown="span">
        ![](../assets/palette_theme/rose_pine_moon.png){ .lightboxOn }
    </figure>

=== "Solarized"

    <figure markdown="span">
        ![](../assets/palette_theme/solarized_light.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/solarized_dark.png#only-dark){ .lightboxOn }
    </figure>

=== "Tokyo Night"

    <figure markdown="span">
        ![](../assets/palette_theme/tokyo_night_day.png#only-light){ .lightboxOn }
        ![](../assets/palette_theme/tokyo_night.png#only-dark){ .lightboxOn }
    </figure>

=== "Tokyo Night Storm"

    <figure markdown="span">
        ![](../assets/palette_theme/tokyo_night_storm.png){ .lightboxOn }
    </figure>

---

### Custom Stylesheet

Applies a custom Qt Style Sheet (`.qss`) file on top of the active style and palette. Leave empty to disable.

---

### Icons

- **Icon Provider**: Selects the icon set.
- **Icon Weight**: Sets the stroke weight. Available weights depend on the selected provider.

---

## Custom Themes

Custom JSON themes can be placed in the `themes` directory inside the settings folder:

=== "Windows"

    ```text
    %LOCALAPPDATA%\vsview\themes\
    ```

=== "Linux"

    ```text
    ~/.config/vsview/themes/
    ```

=== "macOS"

    ```text
    ~/Library/Application Support/vsview/themes/
    ```

Run `vsview config path` to print your active settings directory.

### Format

```json title="custom_theme.json"
{
  "id": "custom_dark",
  "name": "Custom Dark",
  "scheme": "dark",
  "palette": {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    "overlay0": "#6c7086",
    "overlay1": "#7f849c",
    "overlay2": "#9399b2",
    "text": "#cdd6f4",
    "primary": "#89b4fa",
    "secondary": "#f5c2e7",
    "tertiary": "#94e2d5",
    "info": "#89dceb",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
    "danger": "#f38ba8"
  }
}
```

Themes in this directory are loaded automatically on launch.
