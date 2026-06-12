---
icon: lucide/palette
title: Color Management
description: How VSView handles color space conversion and frame properties.
---

# Color Management

When displaying VapourSynth clips, **VSView** must convert the output to packed RGB for rendering on the screen.

By default, this conversion targets the **BT.709** color space (primaries and transfer characteristics) for standard dynamic range (SDR) rendering.

If you are working with high dynamic range content (HDR), VSView supports native end-to-end HDR rendering on HDR-capable displays.

!!! info "No Tone Mapping"

    VSView does not perform any tone mapping of HDR content in either SDR or HDR modes.

    - **In SDR mode**: HDR content and out-of-range values will be clipped to the standard BT.709 gamut.
    - **In HDR mode**: Native linear PQ values are converted directly to linear scRGB and passed to the OS compositor.

        Any tone mapping or luminance mapping (to fit the display's peak brightness) is handled entirely by the OS and the display hardware.

    This behavior affects preview only. It does not change the source clip in your script.

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

    If `_Transfer` or `_Primaries` are missing, no transfer or primaries conversion is performed and a warning is logged instead.

-  **Ignore**

    Follows the same steps as **Warn**, but no warnings are emitted to the log.


## High Dynamic Range (HDR)

VSView supports native HDR display output via high bit-depth color conversion and hardware acceleration.

To preview content in HDR, you must meet the following requirements:

1. **System & Display**: An HDR-capable display must be connected and HDR must be enabled in your operating system settings.
2. **Command Line Flag**: Launch VSView with the [`--hdr` CLI option](configuration.md#-hdr) (or set the `VSVIEW_HDR=true` environment variable).

### Script Configuration

By default, when `--hdr` is enabled, all outputs will attempt HDR conversion.
You can explicitly override this behavior or disable HDR on a per-output basis in your VapourSynth script by passing `hdr=False` to `set_output()`.

```python title="script.vpy"
from vsview import set_output

# Disable HDR for this specific output
set_output(clip, hdr=False)
```
