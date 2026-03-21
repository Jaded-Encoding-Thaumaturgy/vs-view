# Installation

## Prerequisites

- **[Python](https://www.python.org/)** `>=3.12`
- **[VapourSynth](https://www.vapoursynth.com/)** `R69+`
    - **[BestSource](https://github.com/vapoursynth/bestsource)** (optional)

## Installation

Choose your preferred package manager to install `vsview`.
We recommend the **[recommended](https://jaded-encoding-thaumaturgy.github.io/vs-view/plugins/second-party/#installation)** bundle for most users
so that useful plugins are available out of the box.

=== "pip"
    ```bash title="Minimal installation"
    pip install vsview
    ```

    ```bash title="Install with recommended plugins"
    pip install vsview[recommended]
    ```
=== "uv"
    ```bash title="Minimal installation"
    uv add vsview
    ```

    ```bash title="Install with recommended plugins"
    uv add vsview --extra recommended
    ```

## Development Installation

For contributing or local development, see the [Contributing](contributing.md) guide.
