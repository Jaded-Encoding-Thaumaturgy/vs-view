# VSView

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.14%2B-blue)](pyproject.toml)
[![Discord](https://img.shields.io/discord/856381934052704266?label=Discord&logo=discord&logoColor=white)](https://discord.gg/XTpc6Fa9eB)

**The next-generation VapourSynth previewer**

</div>

> [!WARNING] > **Alpha Software**: This project is currently in early alpha. Features are missing, bugs are expected, and the API is subject to breaking changes.

## Installation

### Prerequisites

- **Python**: `>=3.14`
- **VapourSynth**: `R73+`

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
