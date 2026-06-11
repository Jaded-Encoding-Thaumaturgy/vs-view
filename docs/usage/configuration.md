---
icon: lucide/settings
title: Configuration & CLI
description: Command-line options and environment variables for VSView.
---

# Configuration & CLI

VSView can be configured through **command-line arguments** and **environment variables**.

!!! note

    Application settings (appearance, timeline, playback, etc.) are managed
    through the built-in **Settings Dialog** accessible from the menu bar.

---

## Usage

```bash
vsview [OPTIONS] [FILES]... [COMMAND]
```

`[FILES]...`
:   One or more file paths to open. VSView detects the type automatically:

    - `.py` / `.vpy` files open as VapourSynth scripts
    - Everything else opens as a video or image

    If omitted, VSView launches with default workspaces.

---

## Options

#### `--arg` / `-a` `KEY=VALUE`
:   Pass an argument to the script environment. Can be specified multiple times.

    This follows the same convention as `vspipe --arg`.

    ```bash
    vsview script.vpy --arg myparam=hello --arg dryrun=true --arg width=1920
    ```

    The script can then access them via `#!python globals()`:

    ```pycon title="script.vpy"
    >>> globals()
    {
        ...
        'myparam': 'hello',
        'dryrun': 'true'
        'width': '1920',
        ...
    }
    ```

    !!! info

        All values are passed as **strings**.
        Your script is responsible for any type conversion.

#### `--qt-arg` / `-q` `ARG`
:   Pass an argument directly to the underlying Qt (PySide6) application.

    You can specify multiple arguments by repeating the flag or by using a quoted string.

    ```bash
    # Single quoted string (recommended)
    vsview --qt-arg "-platform offscreen -geometry 1920x1080"

    # Multiple flags
    vsview -q -platform -q offscreen -q -geometry -q 1920x1080
    ```

#### `--hdr`
:   Enable High Dynamic Range (HDR) support and configure target graphics API (Direct3D12, Vulkan, Metal).

    **Env:** `VSVIEW_HDR`

#### `--verbose` / `-v`
:   Enable verbose output. Repeat to increase verbosity (`-vv`, `-vvv`).

#### `--version` / `-V`
:   Show the installed vsview version and exit.

---

## Settings Options

These options control how VSView handles its configuration files.

#### `--no-settings`
:   Run without loading or saving any settings for this session.

    **Env:** `VSVIEW_NO_SETTINGS`

#### `--settings-roaming`
:   **Windows only.** Store global settings in `%APPDATA%\vsview\` instead of `%LOCALAPPDATA%\vsview\`.

    **Env:** `VSVIEW_GLOBAL_SETTINGS_ROAMING`

#### `--settings-env`
:   Scope settings to the active Python environment.

    Each environment gets its own subdirectory, preventing conflicts across virtual environments.

    **Env:** `VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT`

#### `--settings-env-copy`
:   If `--settings-env` is set and the scoped file doesn't exist yet, seed it from the base `global_settings.json`.

    **Env:** `VSVIEW_GLOBAL_SETTINGS_ENVIRONMENT_COPY`

---

## Commands

### `vsview settings`

Manage application settings via the CLI.

#### `vsview settings path`
:   Print to stdout the resolved `global_settings.json` path and exit.

    The resolved path respects environment scoping if `--settings-env` is active.

    Default base directory:

    - `%LOCALAPPDATA%\vsview\` on Windows
    - `~/.config/vsview/` on Linux
    - `~/Library/Application Support/vsview/` on macOS

#### `vsview settings wipe`
:   Delete the `global_settings.json` file (as shown by `vsview settings path`) and exit.

    **Options:**

    - `--all`: Delete the entire settings directory (including all environment-scoped subdirectories) and exit.

### `vsview version`

An alternative to the `--version` flag.

---
## Environment Files (.env)

VSView automatically searches for and loads `.env` files on startup.

The search starts from your **current working directory** and moves up through parent directories until a `.env` file is found.

Setting `VSVIEW_NO_DOTENV` will disable .env file loading.
