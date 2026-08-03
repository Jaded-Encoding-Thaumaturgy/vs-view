---
icon: lucide/download
title: Installation
---

# Installation

VSView can be installed either as a Python package using `pip` or `uv` or as a standalone pre-built executable.

## Python Package

Choose your preferred package manager to install `vsview` into an existing Python environment.

We recommend the **[recommended](plugins/second-party.md#installation)** or **[full](plugins/second-party.md#installation)** bundle for most users so that useful plugins are available out of the box.

=== "pip"

    ```bash title="Minimal installation"
    pip install vsview
    ```

    ```bash title="Install with recommended plugins"
    pip install vsview[recommended]
    ```

    ```bash title="Install with all plugins"
    pip install vsview[full]
    ```

=== "uv"

    ```bash title="Minimal installation"
    uv add vsview
    ```

    ```bash title="Install with recommended plugins"
    uv add vsview --extra recommended
    ```

    ```bash title="Install with all plugins"
    uv add vsview --extra full
    ```

---

## Standalone Executable

Pre-built binaries for Windows, macOS, and Linux are published on [GitHub Releases](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/releases).

Release executables are provided in two variants:

- **Online (Lightweight)**: Small download size. Dynamically fetches dependencies on first launch.
- **Offline (Self-Contained)**: Includes an embedded Python runtime and pre-installed workspace packages.
  Requires no external Python installation or internet access.

=== "Windows"

    Download `VSView.exe` (or `VSView-offline.exe`) from [GitHub Releases](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/releases) and launch the executable directly.

=== "macOS"

    1. Download `VSView.dmg` (or `VSView-offline.dmg`) from [GitHub Releases](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/releases).
    2. Open the `.dmg` file and drag **VSView.app** into your `Applications` folder.

=== "Linux"

    1. Download and extract the Linux release archive (`vsview-pyapp-linux-x86_64.tar.gz` or offline variant) from [GitHub Releases](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/releases).
    2. Run the included installation script from the extracted folder:

        ```bash
        bash install.sh
        ```

    This script installs the executable to `~/.local/bin/VSView`, registers standard hicolor icons in `~/.local/share/icons`, and creates a `.desktop` menu launcher.

---

## Development Installation

For contributing, building from source, or local development, see the [Contributing](contributing.md) and [Packaging & Distribution](packaging.md) guides.
