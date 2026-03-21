# VSView

<div align="center" markdown>

![vsview Logo](assets/loading.png){ width="300" }

**The next-generation VapourSynth previewer**

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view)
[![Discord](https://img.shields.io/discord/856381934052704266?label=Discord&logo=discord&logoColor=7F71FF)](https://discord.gg/XTpc6Fa9eB)

</div>

---

## Why VSView

Modern, extensible previewer for [VapourSynth](https://www.vapoursynth.com/),
**VSView** lets you open scripts, videos or images in one interface, making it easier to preview, inspect and compare sources without switching tools.

Built as a modern replacement for [VSPreview](https://github.com/Jaded-Encoding-Thaumaturgy/vs-preview), **VSView** focuses on cleaner, more maintainable code and straightforward plugin integration through a clear API, making official and community extensions easier to build, maintain, and adopt.

## Quick start

Install and launch directly from your terminal:

=== "pip"
    ```bash title="Install and run"
    pip install vsview
    vsview
    ```

    ```bash title="Install with recommended plugins"
    pip install "vsview[recommended]"
    ```

    ```bash title="Open files directly:"
    vsview script.vpy video.mkv
    ```
=== "uv"
    ```bash title="Install and run"
    uv tool install vsview
    vsview
    ```

    ```bash title="Install with recommended plugins"
    uv add vsview --extra recommended
    ```

    ```bash title="Open files directly:"
    uv run vsview script.vpy video.mkv
    ```

## Documentation map

- [Installation](installation.md): Requirements, `pip`/`uv` install, and development setup.
- [Contributing](contributing.md): Development setup and editor recommendations.
- [Usage](usage.md): CLI usage, file detection, workspaces, and shortcuts.
- [Plugins](plugins/index.md): Built-in tools, official plugins, and plugin development.
- [User API](api/user.md): `set_output` and script-side API usage.
- [Developer API](api/developer/index.md): Plugin-facing classes, hooks, and utilities.

## Community and support

Join the [JET Discord](https://discord.gg/XTpc6Fa9eB) for support, feature requests, and discussion.
