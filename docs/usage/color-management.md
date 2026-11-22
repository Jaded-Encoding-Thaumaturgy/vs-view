---
icon: lucide/palette
title: Color Management
description: How VSView handles color space conversion and frame properties.
---

# Color Management

When displaying VapourSynth clips, **VSView** must convert the output to packed RGB for rendering on the screen.

By default, this conversion targets the **BT.709** color space (primaries and transfer characteristics) for standard dynamic range (SDR) rendering,
but this can be configured globally or overridden per-clip.

If you are working with high dynamic range content (HDR), VSView supports native end-to-end HDR rendering on HDR-capable displays.

!!! info "No Tone Mapping"

    VSView does not perform any tone mapping of HDR content in either SDR or HDR modes.

    - **In SDR mode**: HDR content and out-of-range values will be clipped to the target SDR gamut (which defaults to BT.709).
    - **In HDR mode**: Native linear PQ values are converted directly to linear scRGB and passed to the OS compositor.

        Any tone mapping or luminance mapping (to fit the display's peak brightness) is handled entirely by the OS and the display hardware.

    This behavior affects preview only. It does not change the source clip in your script.

## SDR Output Target

Configure the target color space for SDR rendering.

### Global Settings

Under **Settings** > **View**:

- **Output Transfer**: Target transfer characteristics (default: `BT709`).
- **Output Primaries**: Target color primaries (default: `BT709`).

### Script Override

Pass `transfer` and/or `primaries` to `set_output()` to override settings per-clip:

```python title="script.vpy"
import vapoursynth as vs
from vsview import set_output

set_output(clip, transfer=vs.TRANSFER_BT2020_10, primaries=vs.PRIMARIES_BT2020)
```

## Required Properties

VSView relies on VapourSynth frame properties [`_Matrix`](https://www.vapoursynth.com/doc/apireference.html#reserved-frame-properties),
[`_Primaries`](https://www.vapoursynth.com/doc/apireference.html#reserved-frame-properties)
and [`_Transfer`](https://www.vapoursynth.com/doc/apireference.html#reserved-frame-properties)
to properly interpret a clip's color space during conversion.

If these are missing, the conversion may fail or colors may appear incorrect.

!!! info "Explicit Handling"

    VSView does not infer or "guess" missing properties.

    You must ensure your clip has the correct properties set, or configure a fallback policy.

## Frame Property Policy

You can control how VSView reacts to missing properties via the **Settings Dialog**.

!!! danger "YUV Conversion"

    If the clip's color family is `YUV` and `_Matrix` is unknown or unspecified, VSView will always fail to convert the output to RGB.

- **Error (Default)**

    If `_Transfer`, or `_Primaries` are missing, VSView stops rendering and displays a failure image
    because VapourSynth does not have enough information to convert between color spaces.

-  **Warn**

    If `_Transfer` or `_Primaries` are missing, VSView assumes `BT709` for the missing values and a warning is logged.
    The clip is still converted to the **Output Transfer** and **Output Primaries** (or the `set_output()` override).
    So a conversion happens when these targets are not `BT709`, and always in HDR mode.

-  **Ignore**

    Follows the same steps as **Warn**, but no warnings are emitted to the log.


## High Dynamic Range (HDR)

VSView supports native HDR display output via high bit-depth color conversion and hardware acceleration.

To preview content in HDR, you must meet the following requirements:

1. **System & Display**: An HDR-capable display must be connected and HDR must be enabled in your operating system settings.
2. **Command Line Flag**: Launch VSView with the [`--hdr` CLI option](configuration.md#-hdr) (or set the `VSVIEW_HDR=true` environment variable).

!!! warning "Variable Refresh Rate (VRR) on Windows"

    HDR output uses a flip-model swapchain. This lets G-SYNC or FreeSync apply to VSView, even when windowed VRR is off.

    VSView redraws only when something changes, so the refresh rate follows those updates. On OLED panels, this can cause flicker.

    Without `--hdr`, this does not happen.

    If you need HDR on a VRR display, you can:

    - disable G-SYNC / FreeSync while using VSView, or
    - create a driver profile with a fixed refresh rate for `vsview.exe` (or your `python.exe`). Results vary between drivers.

### Script Configuration

By default, when `--hdr` is enabled, all outputs will attempt HDR conversion.
You can explicitly override this behavior or disable HDR on a per-output basis in your VapourSynth script by passing `hdr=False` to `set_output()`.

```python title="script.vpy"
from vsview import set_output

# Disable HDR for this specific output
set_output(clip, hdr=False)
```
