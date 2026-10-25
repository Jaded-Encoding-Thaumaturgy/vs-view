---
icon: lucide/git-pull-request
---

# Contributing

## Development Installation

### Requirements

- **[uv](https://github.com/astral-sh/uv)** is the default tool used for development in this repository.
- For building [`vspackrgb`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/vspackrgb) and [`vsview-histogram`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/histogram), you will need [Cargo](https://doc.rust-lang.org/cargo/getting-started/installation.html).
- For building [`vsview-editor`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/workspaces/editor), you will need [Vite+](https://viteplus.dev/).
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

---

## Packaging & Distribution

VSView can be packaged as a standalone executable using [PyApp](https://github.com/Varde-s-Forks/pyapp) and the custom `vsapp` CLI tool.

For detailed instructions, see the **[Packaging & Distribution](packaging.md)** guide.

---

## Recommended Editor Settings

### VSCode / VSCodium

The settings below configure formatting, type-checking, and file associations consistently across the codebase.

Copy them into your `vsview/.vscode/settings.json`:

```json title="vsview/.vscode/settings.json"
{
  "[github-actions-workflow]": {
    "editor.defaultFormatter": "oxc.oxc-vscode"
  },
  "[json]": {
    "editor.defaultFormatter": "oxc.oxc-vscode",
    "editor.formatOnSave": true,
    "editor.tabSize": 2
  },
  "[python]": {
    "editor.codeActionsOnSave": {
      "source.organizeImports.ruff": "explicit"
    },
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true
  },
  "[rust]": {
    "editor.defaultFormatter": "rust-lang.rust-analyzer",
    "editor.formatOnSave": true
  },
  "[toml]": {
    "editor.defaultFormatter": "tamasfe.even-better-toml",
    "editor.formatOnSave": true
  },
  "editor.formatOnSave": true,
  "files.associations": {
    "*.vpy": "python",
    "**/.github/actions/**/action.yml": "github-actions-workflow",
    "**/.github/workflows/*.yml": "github-actions-workflow"
  },
  "mypy-type-checker.importStrategy": "fromEnvironment",
  "oxc.enable.oxfmt": true,
  "oxc.enable.oxlint": true,
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
  "python.testing.pytestArgs": ["."],
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "rust-analyzer.check.command": "clippy",
  "rust-analyzer.linkedProjects": [
    "src/vspackrgb/Cargo.toml",
    "src/plugins/histogram/src/rust/Cargo.toml"
  ],
  "search.exclude": {
    "**/submodules": true
  },
  "vitest.debugExclude": ["submodules:**", "<node_internals>/**"],
  "vitest.rootConfig": "src/workspaces/editor",
  "vsstubs.watchPlugins": false,
  "yaml.schemas": {
    "https://www.schemastore.org/github-action.json": "**/.github/actions/**/*.yml"
  }
}
```
