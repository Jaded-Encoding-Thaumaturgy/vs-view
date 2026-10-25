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
