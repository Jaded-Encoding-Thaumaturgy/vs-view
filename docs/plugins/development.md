---
icon: lucide/terminal
title: Plugin Development
---

# Plugin Development

This guide covers how to create plugins for VSView, from setting up the basic structure to hooking into the application's lifecycle and rendering loop.

## Plugin Architecture

VSView uses [pluggy](https://pluggy.readthedocs.io/) for its plugin system. Plugins are discovered via Python entry points. The application defines specific specifications (hooks) that your plugin can implement to inject its components or react to events.

### Entry Point Registration

To make your plugin discoverable, register it in your `pyproject.toml` under the `project.entry-points."vsview"` group:

```toml
[project.entry-points."vsview"]
my_plugin = "my_package.plugin:MyPluginHooks"
```

When VSView starts, it will look for this entry point and load the registered hooks.

## Creating a Widget Plugin

The most common type of plugin is a widget plugin, which adds a custom UI panel to the workspace. This is done by subclassing `WidgetPluginBase`.

### Basic Structure

A minimal plugin requires a unique identifier, a display name, and must be registered via the `vsview_register_toolpanel` and/or `vsview_register_tooldock` hook:

```python title="plugin.py"
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from vsview.api import PluginAPI, WidgetPluginBase, hookimpl


class MyPlugin(WidgetPluginBase):
    identifier = "jet_vsview_myplugin"
    display_name = "My Plugin"

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        layout = QVBoxLayout(self)
        self.label = QLabel("Hello from my plugin!")
        layout.addWidget(self.label)


class MyPluginHooks:
    @hookimpl
    def vsview_register_toolpanel(self) -> type[WidgetPluginBase]:
        return MyPlugin
```

## The Hook System

`WidgetPluginBase` provides a robust event hook system that allows your plugin to seamlessly react to interactions and state changes within VSView. You simply override the provided methods in your derived class.

These are just some of the most common hooks available in `WidgetPluginBase`. For a complete list and detailed specifications of all available hooks, see the [API Reference](../api/developer/index.md#vsview.api.WidgetPluginBase).

- `on_current_voutput_changed(...)`: Triggered when the user switches to a different video output tab.
- `on_current_frame_changed(...)`: Triggered continuously as the user scrubs the timeline or during playback.
- `on_playback_started()` / `on_playback_stopped()`: Triggered when video playback begins or pauses.

## Public API

Every `WidgetPluginBase` instance is initialized with a `PluginAPI` object, accessible via the `self.api` attribute. This API is your gateway to the rest of the application.

It allows you to read the current state, interact with the timeline, and access the VapourSynth environment:

```py
class MyPlugin(WidgetPluginBase):
    # ...

    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        # Prevent the logic from running if we are past frame 2000
        if self.api.current_frame > 2000:
            return
        
        # Access the current timeline mode
        if self.api.timeline.mode == "frame":
            print("Viewing by frame!")

```

See the [API reference](../api/developer/#vsview.api.PluginAPI) page for a complete list of capabilities.

## Settings Management

Plugins can define persistent settings that automatically appear in the user's settings panel. VSView natively integrates [Pydantic](https://docs.pydantic.dev/) models to define and validate these configurations.

```python
from typing import Annotated

from jetpytools import fallback
from pydantic import BaseModel
from vsview.api import LocalSettingsModel, PluginSettings, Dropdown


class GlobalSettings(BaseModel):
    mode: Annotated[ # (1)!
        str,
        Dropdown(
            label="Mode",
            items=[("Horizontal", "h"), ("Vertical", "v")],
            tooltip="Stacking direction",
        ),
    ] = "h"
    threshold: int = 50


class LocalSettings(LocalSettingsModel):
    enabled: bool = True
    threshold: int | None = None # (2)!


class MyPlugin(WidgetPluginBase[GlobalSettings, LocalSettings]): # (3)!
    def setup_ui(self) -> None:
        if self.settings.global_.mode == "h":
            ...
        
        self.threshold = fallback(self.settings.local_.threshold, self.settings.global_.threshold)

```

1.  The `#!python Annotated` type allows adding a supported widget to be registered in the settings panel. See the [reference API](../api/developer/#ui-settings) for the different supported widgets.
2. By inheriting from `#!python LocalSettingsModel` and by setting `threshold=None` we make a local threshold value that falls back to the globally defined value above.
3. In order to access the settings and register the models with the VSView settings, make sure the classes are added here as generic types.

## Keyboard Shortcuts

Plugins can easily register custom keyboard shortcuts that integrate with VSView's central shortcut manager. This ensures shortcuts are user-configurable and hot-reloadable.

First, define your shortcuts using `ActionDefinition`. It's easier to group them in an `Enum` or `StrEnum` for readability and type safety:

```python
from enum import StrEnum
from typing import Self

from vsview.api import ActionDefinition

class ShortcutDefinition(StrEnum):
    definition: ActionDefinition

    REMOVE_ITEM = "remove_item", "Remove selected item", "Delete"
    TOGGLE_PLAYBACK = "toggle_playback", "Toggle Playback", "Space"

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        # You must prefix the action ID with your plugin identifier
        obj.definition = ActionDefinition(f"my_plugin.{value}", label, default_key)
        return obj
```

Next, in your `WidgetPluginBase` subclass, declare the `shortcuts` attribute to register the definitions with the settings panel.
Then, map the definitions to your actions or functions using `self.api.register_action` or `self.api.register_shortcut`:

```python hl_lines="8 9 18 19 20 21 22 23 24 25"
from PySide6.QtWidgets import QWidget
from vsview.api import PluginAPI, WidgetPluginBase

class MyPlugin(WidgetPluginBase):
    identifier = "my_plugin"
    display_name = "My Plugin"

    # Register the shortcut definitions to make them user-configurable
    shortcuts = tuple(s.definition for s in ShortcutDefinition)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        # ... setup UI ...
        self.setup_shortcuts()

    def setup_shortcuts(self) -> None:
        # Registering a simple function callback
        self.api.register_shortcut(
            ShortcutDefinition.REMOVE_ITEM.definition,
            self.on_remove_item_triggered,
            self.view_widget, # Parent widget
        )

        # Registering an existing QAction
        self.api.register_action(ShortcutDefinition.TOGGLE_PLAYBACK.definition, self.my_play_action)

    def on_remove_item_triggered(self) -> None:
        ... # Your logic here
```

1. **`shortcuts` attribute**: Exposes the shortcuts to the user application settings panel, allowing them to modify the default keys.
2. **`register_shortcut`**: Binds an `ActionDefinition` directly to a Python callable function.
3. **`register_action`**: Binds an `ActionDefinition` to an existing `QAction`, keeping its tooltip automatically updated with the current key sequence.

## Example: Frame Counter

Putting it all together, here is a complete plugin that displays the current frame number, updates automatically using the hook system, integrates user settings, and registers a custom keyboard shortcut:

```python title="plugin.py"
from enum import StrEnum
from typing import Annotated, Any, Self

from jetpytools import fallback
from pydantic import BaseModel
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from vsview.api import (
    ActionDefinition,
    Dropdown,
    LocalSettingsModel,
    PluginAPI,
    WidgetPluginBase,
    hookimpl,
    run_in_loop,
)


class GlobalSettings(BaseModel):
    font_size: int = 16
    font_weight: Annotated[
        str,
        Dropdown(
            label="Font Weight",
            items=[("Normal", "normal"), ("Bold", "bold")],
            tooltip="The weight of the font",
        ),
    ] = "bold"


class LocalSettings(LocalSettingsModel):
    font_size: int | None = None


class ShortcutDefinition(StrEnum):
    definition: ActionDefinition

    RESET_TEXT = "reset_text", "Reset Display Text", "Ctrl+Alt+R"

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(f"jet_vsview_framecounter.{value}", label, default_key)
        return obj


class FrameCounterPlugin(WidgetPluginBase[GlobalSettings, LocalSettings]):
    identifier = "jet_vsview_framecounter"
    display_name = "Frame Counter"

    # Register the shortcut definitions to make them user-configurable
    shortcuts = tuple(s.definition for s in ShortcutDefinition)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)
        layout = QVBoxLayout(self)
        
        # Read the current settings
        font_size = fallback(self.settings.local_.font_size, self.settings.global_.font_size)
        font_weight = self.settings.global_.font_weight
        
        # Initialize our UI state
        self.label = QLabel(f"Frame: {self.api.current_frame}", self)
        self.label.setStyleSheet(f"font-size: {font_size}px; font-weight: {font_weight};")
        
        layout.addWidget(self.label)
        
        # Setup shortcuts
        self.api.register_shortcut(
            ShortcutDefinition.RESET_TEXT.definition,
            self.on_reset_text,
            self.view_widget,
        )

    def on_reset_text(self) -> None:
        self.update_label(0)

    # Tap into the hook system to react to frame changes
    def on_current_frame_changed(self, n: int) -> None:
        self.update_label(n)

    @run_in_loop # (1)!
    def update_label(self, n: int) -> None:
        self.label.setText(f"Frame: {n}")


class FrameCounterHooks:
    @hookimpl
    def vsview_register_toolpanel(self) -> type[WidgetPluginBase[Any, Any]]:
        return FrameCounterPlugin
```

1. The `@run_in_loop` decorator ensures that the `update_label` method is called in the main Qt event loop, which is required for updating UI elements.
