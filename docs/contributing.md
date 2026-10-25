---
icon: lucide/git-pull-request
---

# Contributing

## Development Installation

### Requirements

- **[uv](https://github.com/astral-sh/uv)** is the default tool used for development in this repository.
- For building [`vspackrgb`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/vspackrgb) and [`vsview-histogram`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/plugins/histogram), you will need [Cargo](https://doc.rust-lang.org/cargo/getting-started/installation.html).
- For building [`vsview-editor`](https://github.com/Jaded-Encoding-Thaumaturgy/vs-view/tree/main/src/workspaces/editor), you will need [Vite+](https://viteplus.dev/) and [pnpm](https://pnpm.io/installation) on your `PATH` (the build hook calls it and is automatically added when Vite+ is installed).
- For working on any TypeScript/JSON/YAML code (formatting, linting, type checking), you will need [Vite+](https://viteplus.dev/).

---

Clone the repository and sync all packages:

```bash
git clone --recurse-submodules https://github.com/Jaded-Encoding-Thaumaturgy/vs-view.git
cd vs-view
uv sync --all-packages --all-groups --all-extras
vp install
```

Run the development version:

```bash
uv run vsview
```

### Code Quality

From the repository root:

- Python:
    ```bash
    uv run ruff check .
    uv run ruff format --check --diff
    uv run mypy .
    uv run pytest
    ```

- TypeScript/JSON/YAML:

    ```bash
    vp check          # lint + format check + type check
    vp check --fix    # apply lint and format fixes
    vp run -r test    # run all TypeScript test suites
    vp run -r build   # build all TypeScript projects
    ```

---

## Packaging & Distribution

VSView can be packaged as a standalone executable using [PyApp](https://github.com/Varde-s-Forks/pyapp) and the custom `vsapp` CLI tool.

For detailed instructions, see the **[Packaging & Distribution](packaging.md)** guide.
