---
icon: lucide/git-pull-request
---

# Contributing

## Development Installation

### Requirements

- **[uv](https://github.com/astral-sh/uv)** is the default tool used for development in this repository.
- For building [`vspackrgb`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/vspackrgb) you will need a working C compiler/toolchain for your platform:
    - Windows: Visual Studio Build Tools (Desktop development with C++)
    - Linux: GCC/Clang and Python headers
    - macOS: Xcode Command Line Tools
- For building [`vsview-cli`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/vsview-cli), you will need [Cargo](https://doc.rust-lang.org/cargo/getting-started/installation.html).

---

Clone the repository and sync all packages:

```bash
git clone --recurse-submodules https://github.com/Jaded-Encoding-Thaumaturgy/vs-view.git
cd vs-view
uv sync --all-packages --all-groups --all-extras
```

Run the development version:

```bash
uv run vsview
```

!!! note "Native Extensions"

    If you are in an environment where you cannot compile native extensions, in `pyproject.toml`:

    - Remove `"src/vspackrgb"` and `"src/vsview-cli"` from `tool.uv.workspace.members`
    - Comment out `vspackrgb = { workspace = true }` and `vsview-cli = { workspace = true }` in `tool.uv.sources`

    You can now run `uv sync` to use the precompiled version from PyPI.

---

## Recommended Editor Settings

### VSCode / VSCodium

The settings below configure formatting, type-checking, and file associations consistently across the codebase.

Copy them into your `vsview/.vscode/settings.json`:

```json title="vsview/.vscode/settings.json"
{
    "[python]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.codeActionsOnSave": {
            "source.organizeImports.ruff": "explicit",
        }
    },
    "[json]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "oxc.oxc-vscode",
        "editor.tabSize": 2
    },
    "[toml]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "tamasfe.even-better-toml"
    },
    "[github-actions-workflow]": {
        "editor.defaultFormatter": "oxc.oxc-vscode"
    },
    "files.associations": {
        "*.vpy": "python"
    },
    "mypy-type-checker.args": [
        "--fixed-format-cache",
        "--config-file .\\pyproject.toml"
    ],
    "mypy-type-checker.importStrategy": "fromEnvironment",
    "python.analysis.autoFormatStrings": true,
    "python.analysis.autoImportCompletions": true,
    "python.analysis.packageIndexDepths": [
        {
            "depth": 2,
            "name": "PySide6"
        }
    ],
    "python.analysis.stubPath": "stubs",
    "python.analysis.typeCheckingMode": "standard",
    "python.analysis.typeEvaluation.deprecateTypingAliases": true,
    "python.analysis.typeEvaluation.enableReachabilityAnalysis": true,
    "python.testing.pytestArgs": [
        "."
    ],
    "python.testing.pytestEnabled": true,
    "python.testing.unittestEnabled": false,
    "search.exclude": {
        "**/submodules": true
    },
}
```
