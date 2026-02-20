# Installation

## Prerequisites

- **[Python](https://www.python.org/)** `>=3.12`
- **[VapourSynth](https://www.vapoursynth.com/)** `R69+`
    - **[BestSource](https://github.com/vapoursynth/bestsource)** (optional)

## Installation

Choose your preferred package manager to install `vsview`. We recommend the **essential** bundle for most users.

=== "pip"
    ```bash title="Standard installation"
    pip install vsview
    ```

    ```bash title="Install essential bundle"
    pip install "vsview[essential]"
    ```
=== "uv"
    ```bash title="Standard installation"
    uv add vsview
    ```

    ```bash title="Add essential bundle"
    uv add vsview --extra essential
    ```

- [FrameProps Extended](plugins/second-party.md#frameprops-extended) — Enhanced frame properties categories and formatting
- [Split Planes](plugins/second-party.md#split-planes) — View individual clip planes

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

!!! note "Cython Extensions"

    If you are in an environment where you cannot compile Cython extensions, in `pyproject.toml`:
    
    - Remove `"src/vspackrgb"` from `tool.uv.workspace.members`
    - Comment out `vspackrgb = { workspace = true }` in `tool.uv.sources`
  
    You can now run `uv sync` to use the precompiled version from PyPi.
