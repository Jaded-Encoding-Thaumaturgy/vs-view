# VSView

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![Lint](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/actions/workflows/lint.yml/badge.svg)](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/actions/workflows/lint.yml)
[![Discord](https://img.shields.io/discord/856381934052704266?label=Discord&logo=discord&logoColor=7F71FF)](https://discord.gg/XTpc6Fa9eB)

<img src="src/vsview/assets/loading.png" height="200"/>

**The next-generation VapourSynth previewer**

</div>

<!-- prettier-ignore -->
> [!WARNING]
> **Alpha Software**: This project is currently in early alpha. Features are missing, bugs are expected, and the API is subject to breaking changes.

## Installation

### Prerequisites

- **Python**: `>=3.12`
- **VapourSynth**: `R69+`

## Usage

Once installed, you can launch the previewer using the command line:

```bash
vsview
```

You can also run it with a generic VapourSynth script:

```bash
vsview script.vpy
```

## Development

This project uses `uv` for dependency management and workflow.

```bash
uv sync --all-extras --all-groups
uv run vsview
```

## Contributing

Contributions are welcome! Please check the [Discord server](https://discord.gg/XTpc6Fa9eB) or open an issue to discuss planned features.
