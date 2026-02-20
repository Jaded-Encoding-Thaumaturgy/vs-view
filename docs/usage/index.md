# Usage

This page covers the basic usage of **VSView**: launching the app, opening files, and exposing outputs from scripts.

## Quick Start

Open **VSView** with default workspaces:

=== "pip"
    ```bash
    vsview
    ```
=== "uv"
    ```bash
    uv run vsview
    ```

Open one or more inputs directly:

=== "pip"
    ```bash
    vsview script.vpy
    vsview video.mkv script.vpy image.png
    ```
=== "uv"
    ```bash
    uv run vsview script.vpy
    uv run vsview video.mkv script.vpy image.png
    ```

## CLI Input Types

**VSView** detects input type from file extension:

| Extension                            | Type               |
| ------------------------------------ | ------------------ |
| `.py`, `.vpy`                        | VapourSynth script |
| `.mkv`, `.mp4`, `.png`, `.jpg`, etc. | Video & Image file |

You can mix these in a single command to open multiple sources at once. Each input will create a new workspace.

## Using **VSView** in VapourSynth Scripts

Use `set_output()` to register clips with readable names:

```python
import vapoursynth as vs
from vsview import set_output

core = vs.core
clip = core.bs.VideoSource("video.mkv")

set_output(clip)
set_output(clip.std.Invert(), "Inverted")
```

## Keyboard Shortcuts

Keyboard shortcuts are customizable from the settings menu.