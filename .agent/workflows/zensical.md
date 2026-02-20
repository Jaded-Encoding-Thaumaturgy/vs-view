---
description: Zensical Documentation Workflows
---

# Zensical Documentation Workflows

This workflow provides automated steps for managing documentation with Zensical, the official successor to mkdocstrings.

## 1. Add New Documentation Page

Use this to create a new page and automatically register it in `zensical.toml`.

- Create the `.md` file in the appropriate directory.
- Add standard Zensical frontmatter (icon, title).
- Update the `nav` section in `zensical.toml`.

## 2. Generate API Reference

// turbo

- Identify the Python module to document.
- Create an API reference page (e.g., `docs/api/module_name.md`).
- Add the `::: module.path` identifier for `mkdocstrings`.
- Add the page to the "API Reference" section of the navigation.

## 3. Preview Documentation

// turbo

- Run `uv run zensical serve` to start the local development server.

## 4. Build for Production

// turbo

- Run `uv run zensical build` to generate the static site.

## 5. Sync Navigation

- Scan the `docs/` directory.
- Ensure all `.md` files are represented in `zensical.toml` or confirm they are intentionally hidden.
