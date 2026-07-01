---
icon: lucide/blocks
title: Second-Party Plugins
---

# Second-Party Plugins

Second-party plugins are officially maintained but distributed as separate packages to keep the core installation lightweight.

## Overview

<div class="grid cards" markdown>

- :lucide-audio-lines: **Audio Convert**

    ---

    An [AudioNode](https://www.vapoursynth.com/doc/pythonreference.html#vapoursynth.AudioNode) processor for converting audio sample types and resampling audio clips for playback.

    [:lucide-move-right: Details](#audio-convert) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/audio-convert)

- :lucide-split-square-horizontal: **Comparison**

    ---

    Select, extract, and upload comparison frames to Slow.pics with TMDB metadata integration.

    [:lucide-move-right: Details](#comparison) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/comp)

- :lucide-activity: **FFT Spectrum**

    ---

    Displays the Fast Fourier Transform (FFT) spectrum of all the planes of a video clip.

    [:lucide-move-right: Details](#fft-spectrum) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/fftspectrum)

- :lucide-list-plus: **FrameProps Extended**

    ---

    Extends the built-in [Frame Properties](first-party.md#frame-properties) tool with specialized categories and formatters.

    [:lucide-move-right: Details](#frameprops-extended) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/frameprops-extended)

- :lucide-bar-chart-2: **Histogram**

    ---

    Per-frame video scopes: Levels, Luma, Vectorscope, and Waveform.

    [:lucide-move-right: Details](#histogram) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/histogram)

- :lucide-layers-2: **Split Planes**

    ---

    Visualize individual planes (e.g., Y, U, V) of a video clip to inspect channel-specific artifacts.

    [:lucide-move-right: Details](#split-planes) · [:fontawesome-brands-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/split-planes)

</div>

---

## Installation

Second-party plugins are officially maintained but distributed as separate Python packages. Note that many of these also require specific **native VapourSynth plugins** to be installed in your VapourSynth environment.

You can install plugins individually or choose one of the pre-configured bundles.

!!! tip "Optional: Recommended & Full Bundles"
    * The **recommended** bundle includes **Split Planes**, **FrameProps Extended**, and **Comparison**.
    * The **full** bundle includes everything in the **recommended** bundle, plus **Audio Convert**, **Histogram**, and the third-party [**Native Resolution**](third-party.md#native-resolution) plugin.

    === "pip"
        ```bash title="Install recommended bundle"
        pip install vsview[recommended]
        ```

        ```bash title="Install full bundle"
        pip install vsview[full]
        ```
    === "uv"
        ```bash title="Add recommended bundle"
        uv add vsview --extra recommended
        ```

        ```bash title="Add full bundle"
        uv add vsview --extra full
        ```

Detailed installation for individual packages can be found in their respective sections below.

---

## Audio Convert [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/audio-convert){ title="Source Code" }

=== "pip"
    ```bash title="Install Audio Convert"
    pip install vsview-audio-convert
    ```
=== "uv"
    ```bash title="Add Audio Convert"
    uv add vsview-audio-convert
    ```

The Audio Convert plugin integrates a specialized [AudioNode](https://www.vapoursynth.com/doc/pythonreference.html#vapoursynth.AudioNode) processor into the **VSView** pipeline for reconciling differences between script audio and system playback capabilities.

### Available Options
- Sample type conversion
- Sample rate conversion
- SoX Quality presets

### VapourSynth Requirements
- [**ares**](https://github.com/ropagr/VS-AudioResample): Required for SoX quality presets and higher-quality resampling.
- [**atools**](https://github.com/ropagr/VS-AudioTools): Fallback for basic sample type conversion if `ares` is not installed.

---

## Comparison [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/comp){ title="Source Code" }

=== "pip"
    ```bash title="Install Comparison"
    pip install vsview-comp
    ```
=== "uv"
    ```bash title="Add Comparison"
    uv add vsview-comp
    ```

The Comparison plugin provides an integrated workflow for extracting frames and uploading them directly to [Slow.pics](https://slow.pics/).

It features automated frame selection, filtering by picture type, and integration with TMDB.

### Features
- **Frame Selection**:
    - Choose frames manually or automatically based on frame count and time range.
    - Filter by picture types (I/P/B-frames) and combed frames.
    - Select the darkest and/or lightest frames using asymmetric Gaussian brightness scoring.
- **Probability Curve**:
    - Bias which part of the timeline random frames are drawn from using an interactive curve editor.
    - Click **Curve...** next to the random frame count to open the editor.
- **TMDB Integration**:
    - Search and retrieve metadata from TMDB to automatically populate collection names.
    - Customize the naming format in the plugin **Settings**.
- **Direct Upload**:
    - Upload extracted frames to [Slow.pics](https://slow.pics/).
    - Configure login in the plugin **Settings** to upload directly.

### Settings

| Setting                    | Default  | Description                                                                                     |
| :------------------------- | :------- | :---------------------------------------------------------------------------------------------- |
| **Browser ID**             | auto     | UUID sent to Slow.pics on every upload. (see [Browser ID](#browser-id)).                        |
| **Allowed frame searches** | 150      | Max attempts per frame slot before giving up.                                                   |
| **Brightness candidates**  | 0 (auto) | Candidate frames to analyze for dark/light selection. `0` auto-scales from clip length and FPS. |

### Probability Curve Editor

Click **Curve...** (next to the random frame count) to open the curve editor and bias where in the timeline random frames are sampled from.

| Action                                   | Effect                                         |
| :--------------------------------------- | :--------------------------------------------- |
| Double-click on the graph                | Add a control point                            |
| Drag a point                             | Adjust its position and weight (Y-axis, 0 – 5) |
| Right-click / ++delete++ / ++backspace++ | Remove the point                               |
| **Reset**                                | Restore flat uniform distribution              |

The boundary points (start/end frame) are fixed horizontally but their weights are editable. Y = 1 everywhere gives uniform sampling.

### Dark / Light Frame Selection

Candidate frames are scored with an **asymmetric Gaussian** on their average luma (`PlaneStatsAverage`):

- **Dark**: peak ≈ 6.5 % (σ left = 0.015, σ right = 0.15)
- **Light**: peak ≈ 75 % (σ left = 0.45, σ right = 0.90)

### Browser ID

Every upload is tagged with a persistent **Browser ID** UUID so Slow.pics can associate it with your browser session.

!!! tip "Claiming an anonymous upload"
    If you uploaded without being logged in, you can still claim the comparison afterwards:

    1. Copy your **Browser ID** from the plugin **Settings** (clipboard button).
    2. In your browser's dev tools (++f12++), go to **Application → Cookies → `slow.pics`** and set `BROWSER-ID` to that value.
    3. Reload the comparison page and claim it from the page menu.

    When uploading while logged in, Slow.pics returns its own `BROWSER-ID`; the plugin adopts it automatically.

### Script Integration

All `set_output` outputs are available by default. Pass `allow_comp` to explicitly exclude specific outputs.

### VapourSynth Requirements
- [**fpng**](https://github.com/Mikewando/vsfpng) (Optional): For slightly faster frame extraction.

---

## FFT Spectrum [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/fftspectrum){ title="Source Code" }

=== "pip"
    ```bash title="Install FFT Spectrum"
    pip install vsview-fftspectrum
    ```
=== "uv"
    ```bash title="Add FFT Spectrum"
    uv add vsview-fftspectrum
    ```

The FFT Spectrum tool provides a visualization of the Fast Fourier Transform (FFT) spectrum of all the planes of a video clip.

---

## Histogram [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/histogram){ title="Source Code" }

=== "pip"
    ```bash title="Install Histogram"
    pip install vsview-histogram
    ```
=== "uv"
    ```bash title="Add Histogram"
    uv add vsview-histogram
    ```

Provides per-frame video scopes as tabs inside a single panel. 

- Updates are paused during playback and resume on the active tab when playback stops.
- Right-click any plot to copy or save the view.

!!! note
    The Luma tab uses [Numba](https://numba.pydata.org/) JIT compilation. A background pre-warm runs at startup; the tab is inactive until it completes.

### Levels

Plots a per-plane pixel value histogram for the current frame.

| Control               | Options              | Description                                                                             |
| :-------------------- | :------------------- | :-------------------------------------------------------------------------------------- |
| **Bin resolution**    | Auto, 256, 512, 1024 | Number of histogram bins. Auto scales to the panel width.                               |
| **Clamp factor**      | 0.001 % – 100 %      | Clips peak bin counts to this percentage of total pixels, making smaller peaks visible. |
| **Show unsafe zones** | on / off             | Highlights broadcast-illegal ranges (YUV limited range only).                           |

### Luma

Displays a luma scope using a VapourSynth `ModifyFrame` node, rendered through the standard graphics view.

| Control               | Options                      | Description                                                         |
| :-------------------- | :--------------------------- | :------------------------------------------------------------------ |
| **Frequency (Shift)** | 2 – 256 cycles (shift 1 – 8) | Controls the number of luma cycles displayed across the scope.      |
| **Sawtooth style**    | on / off                     | Switches the rendering style from sine-like to a sawtooth waveform. |

Only GRAY and YUV inputs are supported. RGB input shows an error overlay.

### Vectorscope

Plots chroma (U/V) distribution on a 2D plane.

Graticules show 75 % and 100 % saturation circles, a skin-tone reference line, and primary/secondary color targets
that automatically adjust based on the clip's color matrix (supporting Rec. 601, Rec. 709, Rec. 2020, and ST 240M).

| Control        | Options                                | Description                                                                                                                                           |
| :------------- | :------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Mode**       | Density, Chroma Wheel, Pixel Color     | **Density**: logarithmic heat-map with a phosphor color table.<br>**Chroma Wheel**: density map drawn over a full-color UV background wheel.<br>**Pixel Color**: each pixel plotted at its actual RGB-converted color. |
| **Resolution** | Auto, 256, 512, 1024                   | Size of the internal scope image (square). Auto caps to the bit depth limit.                                                                           |
| **Matrix**     | Auto, BT.709, BT.601, BT.2020, ST 240M | Color matrix coefficients used for target graticules and signal conversion. Auto detects from clip properties or resolution.                            |
| **Luma**       | 0.01 – 2.00                            | Brightness scaling factor applied to the point cloud/glow. Only active in `Chroma Wheel` mode.                                                      |

RGB input is not supported and shows a warning. GRAY input plots all pixels at neutral chroma.

### Waveform

Plots pixel values column-by-column as a waveform. Logarithmic density scaling is applied per-column.

| Control          | Options              | Description                                                                                                             |
| :--------------- | :------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| **Mode**         | Luma, RGB/YUV Parade | **Luma**: single Y-plane waveform.<br>**Parade**: one waveform per plane side-by-side (R/G/B or Y/U/V, depending on color family). |
| **Resolution**   | Auto, 256, 512, 1024 | Vertical resolution of the scope. Auto caps to the bit depth limit.                                                     |
| **Show zones**   | on / off             | Overlays the neutral line and broadcast-limit lines (16/235 for luma, 16/240 for chroma) with shaded unsafe regions.    |
| **Dynamic gain** | on / off             | When on, scales brightness relative to the densest column. When off, scales relative to frame height.                   |
| **Gain**         | 0.1x – 10.0x         | Multiplier applied on top of the logarithmic scale.                                                                     |

### CIE Chromaticity

Plots the chromaticity coordinates of the frame pixels onto a standard CIE chromaticity diagram (CIE 1931 xy or CIE 1976 u'v'). This allows visualizing the gamut coverage and color distribution of the video.

Graticules show the boundary triangles for common color gamuts (**Rec. 709**, **Rec. 601**, **DCI-P3**, and **Rec. 2020**) and a reference crosshair for the **D65** white point.

| Control        | Options                               | Description                                                                                                                                                                                                                                                |
| :------------- | :------------------------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **CIE Mode**   | CIE 1931 (xy), CIE 1976 (u'v')        | Toggles between the CIE 1931 standard color space representation and the CIE 1976 Uniform Chromaticity Scale (UCS) representation.                                                                                                                         |
| **Render Mode**| Density, Chroma Wheel, Pixel Color    | **Density**: logarithmic heat-map showing pixel density using a phosphor color table.<br>**Chroma Wheel**: density map drawn as a white glow over a full-color background chromaticity diagram.<br>**Pixel Color**: each pixel plotted at its actual RGB color, scaled by density and luma settings. |
| **Resolution** | Native, 256x256, 512x512, 1024x1024   | Size of the internal scope image. `Native` uses the height of the frame.                                                                                                                                                   |
| **Luma**       | 0.01 – 2.00                           | Brightness scaling factor applied to the point cloud/glow. Only active in `Pixel Color` and `Chroma Wheel` modes.                                                                                                                                          |
| **Gamuts**     | Rec. 709, Rec. 601, DCI-P3, Rec. 2020 | Checkboxes to toggle the display of the target gamut boundary triangles and labels on the diagram.                                                                                                                                                         |

Only RGB input is supported. Other formats, or clips without a valid `_Primaries` frame property, will display an error overlay.

---

## FrameProps Extended [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/frameprops-extended){ title="Source Code" }

=== "pip"
    ```bash title="Install FrameProps Extended"
    pip install vsview-frameprops-extended
    ```
=== "uv"
    ```bash title="Add FrameProps Extended"
    uv add vsview-frameprops-extended
    ```
This plugin adds more categories and formatters to the built-in [Frame Properties](first-party.md#frame-properties) panel.

---

## Split Planes [ :fontawesome-brands-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/split-planes){ title="Source Code" }

=== "pip"
    ```bash title="Install Split Planes"
    pip install vsview-split-planes
    ```
=== "uv"
    ```bash title="Add Split Planes"
    uv add vsview-split-planes
    ```

Split Planes splits a video clip into its individual planes for inspection.

### Features
- Extends the Graphics View's default context menu to provide a way to offset chroma plane values.
