# VSView

<div align="center">

<img src="https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/raw/main/src/vsview/assets/loading.png" height="200"/>

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![Lint](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/actions/workflows/lint.yml/badge.svg)](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/actions/workflows/lint.yml)
[![Discord](https://img.shields.io/discord/856381934052704266?label=Discord&logo=discord&logoColor=7F71FF)](https://discord.gg/XTpc6Fa9eB)

**The next-generation VapourSynth previewer**

</div>

<!-- prettier-ignore -->
> [!WARNING]
> **Alpha Software**: This project is currently in early alpha. Features are missing, bugs are expected, and the API is subject to breaking changes.

## Installation

### Prerequisites

- **[Python](https://www.python.org/)** `>=3.12`
- **[VapourSynth](https://www.vapoursynth.com/)** `R69+`
    - **[BestSource](https://github.com/vapoursynth/bestsource)** (optional)

### Install with pip

The quickest way to install `vsview`:

```bash
pip install vsview
```

For the recommended setup with commonly used plugins:

```bash
pip install vsview[essential]
```

### Install with uv

If you use [uv](https://docs.astral.sh/uv/) for package management:

```bash
uv add vsview
```

With essential plugins:

```bash
uv add "vsview[essential]"
```

## Contributing

Contributions are welcome! Please check the [Discord server](https://discord.gg/XTpc6Fa9eB) or open an issue to discuss planned features.

## Development Installation

For contributing or local development:

```bash
git clone --recurse-submodules https://github.com/Jaded-Encoding-Thaumaturgy/vs-view.git
cd vs-view
uv sync --all-packages
```

Run the development version:

```bash
uv run vsview
```

<!-- prettier-ignore -->
> [!NOTE]
> If you are in an environment where you cannot compile Cython extensions, remove `"src/vspackrgb"` from `members`
> and comment out `vspackrgb = { workspace = true }` in `pyproject.toml` before running `uv sync`
>  to use the precompiled version from PyPI.
