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

    [:lucide-move-right: Details](#audio-convert) · [:lucide-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/audio-convert)

- :lucide-split-square-horizontal: **Comparison**

    ---

    Select, extract, and upload comparison frames to Slow.pics with TMDB metadata integration.

    [:lucide-move-right: Details](#comparison) · [:lucide-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/comp)

- :lucide-activity: **FFT Spectrum**

    ---

    Displays the Fast Fourier Transform (FFT) spectrum of all the planes of a video clip.

    [:lucide-move-right: Details](#fft-spectrum) · [:lucide-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/fftspectrum)

- :lucide-list-plus: **FrameProps Extended**

    ---

    Extends the built-in [Frame Properties](first-party.md#frame-properties) tool with specialized categories and formatters.

    [:lucide-move-right: Details](#frameprops-extended) · [:lucide-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/frameprops-extended)

- :lucide-layers-2: **Split Planes**

    ---

    Visualize individual planes (e.g., Y, U, V) of a video clip to inspect channel-specific artifacts.

    [:lucide-move-right: Details](#split-planes) · [:lucide-github: Source](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/split-planes)

</div>

---

## Installation

Second-party plugins are officially maintained but distributed as separate Python packages. Note that many of these also require specific **native VapourSynth plugins** to be installed in your VapourSynth environment.

You can start with the **Recommended Bundle**.

!!! tip "Optional: Recommended Bundle"
    The `recommended` bundle includes **Split Planes**, **FrameProps Extended**, and **Comparison**.
    
    === "pip"
        ```bash title="Install recommended bundle"
        pip install "vsview[recommended]"
        ```
    === "uv"
        ```bash title="Add recommended bundle"
        uv add vsview --extra recommended
        ```

Detailed installation for individual packages can be found in their respective sections below.

---

## Audio Convert [ :lucide-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/audio-convert){ title="Source Code" }

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

## Comparison [ :lucide-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/comp){ title="Source Code" }

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
    - Auto-select based on frame brightness (darkest/lightest).
- **TMDB Integration**:
    - Search and retrieve metadata from TMDB to automatically populate collection names.
    - Customize the naming format in the plugin settings.
- **Direct Upload**:
    - Upload extracted frames directly to [Slow.pics](https://slow.pics/).
    - Configure login in the plugin settings to upload directly.

### VapourSynth Requirements
- [**fpng**](https://github.com/Mikewando/vsfpng) (Optional): For slightly faster frame extraction.

---

## FFT Spectrum [ :lucide-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/fftspectrum){ title="Source Code" }

=== "pip"
    ```bash title="Install FFT Spectrum"
    pip install vsview-fftspectrum
    ```
=== "uv"
    ```bash title="Add FFT Spectrum"
    uv add vsview-fftspectrum
    ```

The FFT Spectrum tool provides a visualization of the Fast Fourier Transform (FFT) spectrum of all the planes of a video clip.

### VapourSynth Requirements
- [**fftspectrum_rs**](https://github.com/sgt0/vapoursynth-fftspectrum-rs)

---

## FrameProps Extended [ :lucide-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/frameprops-extended){ title="Source Code" }

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

## Split Planes [ :lucide-github: ](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/split-planes){ title="Source Code" }

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

### VapourSynth Requirements
- [**akarin**](https://github.com/AkarinVS/L-SMASH-Works-Akarin)
