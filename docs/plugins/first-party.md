---
icon: lucide/package-check
title: First-Party Plugins
---

# First-Party Plugins

First-party plugins are official components integrated directly into the **VSView** source code, providing essential analysis tools that are guaranteed to be compatible with every release.

<div class="grid cards" markdown>

- :lucide-pipette: **Color Picker**

    ---

    Inspect precise pixel values directly from the viewport. View both rendered RGB and raw source values in real-time.

    [:lucide-move-right: Explore Color Picker](#color-picker)

- :lucide-list-video: **Frame Properties**

    ---

    Access frame properties attached by VapourSynth filters.

    [:lucide-move-right: Explore Frame Properties](#frame-properties)

- :lucide-clapperboard: **Scening**

    ---

    Create, manage, and export scene ranges. Supports custom parsers and serializers for professional workflows.

    [:lucide-move-right: Explore Scening](#scening)

</div>

---

## Color Picker

The Color Picker dock tool allows you to inspect precise pixel values at any position in the frame.

<figure markdown="span">
    ![Color Picker dock showing source and rendered pixel values](../assets/colorpicker_dock.png){ loading=lazy }
</figure>

### How to Use

1.  Click the **Pipette icon** in the toolbar to activate the tool.
2.  **Hover the mouse** over the viewport to see real-time pixel data.
3.  **Right-click** or click the tool icon again to deactivate.

### Data Provided
The inspector provides two sets of values for the selected pixel:

- **Rendered Values:** The RGB values as they appear after the internal **VSView** rendering pipeline.
- **Source Values:** The raw, underlying sample values directly from the VapourSynth clip.

---

## Frame Properties

The Frame Properties panel displays metadata attached to each frame by VapourSynth filters.

<figure markdown="span">
    ![Frame Properties panel showing metadata for the current frame](../assets/frameprops_panel.png){ loading=lazy }
</figure>

### Features
- **Categorization:** Properties are grouped into logical categories (e.g., Video, Metrics, Field).
- **Formatters:** Values are automatically formatted into readable strings based on their type.
- **Context Menu:** Right-click a property to display a context menu with options to copy the property value or its key.

!!! Abstract "Extended Functionality"
    If you need even more detailed property tracking, check out the [FrameProps Extended](second-party.md#frameprops-extended) second-party plugin.

---

## Scening

The Scening dock tool is designed for creating, managing, and exporting scene ranges.
<figure markdown="span">
    ![Scening dock showing scene ranges](../assets/scening_dock.png){ loading=lazy }
</figure>

### Scene Management

The upper panel manages **scenes**, which are named containers for ranges. Each scene has a customizable color, can be bound to specific outputs, and has a display toggle to control timeline visibility.

- **:lucide-plus: Create new scene** adds a new scene.
- **:lucide-x: Delete** removes a scene.
- The **Color** swatch changes the scene's color.
- The **Outputs** column restricts a scene to specific video outputs.
- The **Display** checkbox toggles the scene's visibility on the timeline.

### Adding Ranges

The lower panel shows the ranges belonging to the selected scene. You build ranges using the **Mark in / Mark out** workflow:

1. Navigate to the desired start frame on the timeline.
2. Press ++q++ (or click **Mark in**) to set the start point.
3. Navigate to the desired end frame.
4. Press ++w++ (or click **Mark out**) to set the end point.
5. Press ++e++ (or click **Add range**) to commit the range to the selected scene.

Each range displays its **Start Frame**, **End Frame**, **Start Time**, **End Time**, and an optional **Label**.

### Keyboard Shortcuts

| Shortcut             | Action                           |
| :------------------- | :------------------------------- |
| ++q++                | Toggle range start (Mark in)     |
| ++w++                | Toggle range end (Mark out)      |
| ++e++                | Validate / add the pending range |
| ++del++              | Remove selected range or scene   |
| ++ctrl+left++        | Seek to previous range boundary  |
| ++ctrl+right++       | Seek to next range boundary      |
| ++ctrl+shift+left++  | Seek to previous range           |
| ++ctrl+shift+right++ | Seek to next range               |

Each shortcut is customizable in the **Settings** menu.

### Timeline Integration

When a scene's **Display** checkbox is enabled, its ranges are drawn as colored notches directly on the timeline.
Hovering over a notch displays a tooltip with the range label (if set).
Scenes can be filtered per output, so only relevant ranges are shown for the active video tab.

### Import & Export

The toolbar provides **Import scene...** and **Export scene...** buttons for working with external files.

#### Supported Import Formats

| Format | Extension |
| :--- | :--- |
| Aegisub Advanced SSA | `.ass` |
| OGM Chapters | `.txt` |
| Matroska XML Chapters | `.xml` |
| XviD Log | `.txt`, `.log` |
| QP File | `.qp`, `.txt` |
| Wobbly File | `.wob` |
| Python List (Frames) | `.txt` |
| Python List (Timestamps) | `.txt` |

#### Supported Export Formats

| Format | Extension |
| :--- | :--- |
| OGM Chapters | `.txt` |
| Python List (Frames) | `.txt` |
| Python List (Timestamps) | `.txt` |

!!! Abstract "Extended Functionality"
    Developers can register custom parsers and serializers via the plugin hook system.
    See the [Scening Tool API](../api/developer/tools/scening.md) for details.
