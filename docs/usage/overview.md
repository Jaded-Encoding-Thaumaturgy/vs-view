---
icon: lucide/book-open
---

# Usage Overview

This page provides an overview of the **VSView** interface and its core components.

## Quick Start: Launching VSView

### Command Line Interface

**VSView** detects input types automatically based on file extensions.

| Extension                            | Type               |
| ------------------------------------ | ------------------ |
| `.py`, `.vpy`                        | VapourSynth script |
| `.mkv`, `.mp4`, `.png`, `.jpg`, etc. | Video & Image file |

```bash
# Open VSView
vsview

# Open specific files
vsview script.vpy video.mkv
```

!!! tip "Advanced CLI options & environment variables"

    VSView supports additional flags for settings management and environment scoping.
    See the [Configuration & CLI](configuration.md) page for the full reference.

### Registering Outputs in Scripts

Register clips in VapourSynth scripts using `set_output()`:

```python title="example.vpy"
import vapoursynth as vs
from vsview import set_output

core = vs.core
clip = core.bs.VideoSource("video.mkv")

set_output(clip, "Source")
set_output(clip.std.Invert(), "Inverted")
```


## Interface at a Glance

**VSView** uses a workspace-based layout to organize scripts and files and provides access to integrated tools.

<figure markdown="span">
    ![](../assets/app_overview.png){ .lightboxOn }
</figure>

---

## 1. The Sidebar

The sidebar on the left is the main navigation area. Each open workspace is represented by an icon.
It can be toggled on or off from the **View** menu if more space is needed for the content area.

### Navigation & Management
- **Switching Workspaces**: Click an icon to switch the main content area to that workspace.
- **Reordering**: You can click and drag icons to move them up or down.
- **Context Menu**: Right-click any sidebar icon to access workspace-specific actions:
    - **Reload**: Refresh the active workspace.

        !!! info "Hot Reloading"
            When reloading, only **local** Python modules are refreshed.

            Standard library modules and installed packages (those found in `site-packages`) are skipped to ensure environment stability.

    - **Clear**: Reset the workspace to its default state.
    - **Delete**: Close the workspace and free up its resources.

---

## 2. The Menu Bar

The top menu bar provides global controls that affect the entire application.

### `New` Menu
Bring new content into **VSView**:

- **`Load Script...`**: Open a VapourSynth (`.vpy`) or Python (`.py`) script.
- **`Load File...`**: Open a video or image file.
- **Workspace Submenu**: Manually create an empty workspace of a specific type.

### `View` Menu
Manage your workspace environment:

- **`Sidebar`**: Toggle the visibility of the primary navigation sidebar.
- **`Tool Docks`**: Toggle the visibility of persistent dock tools.
- **`Tool Panels`**: Toggle the visibility of tabbed tools.

### `Settings`
Opens the **Settings Dialog**, where you can customize shortcuts, application and plugin settings.

---

## 3. The Main Content Area

The main area displays the active workspace's content.
See [Workspaces](workspaces/workspace.md) for more information.
