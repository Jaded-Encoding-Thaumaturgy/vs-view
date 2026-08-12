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

## Workspace Options

#### `--workspace` / `-w` `WORKSPACE`
:   Open a specific workspace on startup. Can be specified multiple times to open several at once.

    The value is a **workspace slug**. The workspace title lowercased with spaces replaced by hyphens. Built-in slugs:

    | Slug           | Workspace               |
    | :------------- | :---------------------- |
    | `script`       | Python Script workspace |
    | `file`         | Video File workspace    |
    | `quick-script` | Quick Script workspace  |

    Plugin-provided workspaces are also accepted if the plugin is installed.

    ```bash
    # Open two workspaces on startup
    vsview --workspace script --workspace file
    ```

#### `--no-default-workspace`
:   Start the app without opening any workspace.

    Suppresses the three default workspaces (Python Script, Video File, Quick Script)
    that are normally opened when no files or `--workspace` flags are provided.

    **Env:** `VSVIEW_NO_DEFAULT_WORKSPACE`

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

## Logging Options

These options control application log verbosity.

#### `--file-log`
:   Enable file logging in the platform's standard log directory.

    - `%LOCALAPPDATA%\vsview\Logs` on Windows
    - `~/.local/state/vsview/log` on Linux
    - `~/Library/Logs/vsview` on macOS

    **Env:** `VSVIEW_FILE_LOG`

#### `--vapoursynth-log-level` `LEVEL`
:   Set the log level for the VapourSynth core environment.
    Available levels: `critical`, `error`, `warning`, `info`, `debug`, `notset`.

    Default to `INFO` or verbosity level if `--verbose` is specified.

#### `--vsengine-log-level` `LEVEL`
:   Set the log level for the VSEngine subsystem. Default to `INFO`. Available levels: `critical`, `error`, `warning`, `info`, `debug`, `notset`.

#### `--qt-log-level` `LEVEL`
:   Set the log level for Qt / PySide6 system messages. Available levels: `critical`, `error`, `warning`, `info`, `debug`, `notset`.

    Default to `INFO` or verbosity level if `--verbose` is specified.

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

## Standalone Environment Management (`env`)

Pre-built standalone executables feature an embedded Python runtime managed via the `vsview env` CLI command namespace.

### Subcommands

#### `vsview env pip` `[ARGS]...`
:   Directly invoke `pip` using the standalone executable's embedded Python runtime.

    Use this command to install VapourSynth plugins or other Python dependencies:

    === "PowerShell (Windows)"

        ```powershell
        # Install additional plugins or package bundles
        .\VSView.exe env pip install vsjetpack[full,nvidia] --extra-index-url https://pypi.nvidia.com/

        # List installed packages in the executable environment
        .\VSView.exe env pip list

        # Uninstall a package
        .\VSView.exe env pip uninstall <package_name>
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        # Install additional plugins or package bundles
        ./VSView env pip install "vsjetpack[full,gpu,cl,vulkan]" 

        # List installed packages in the executable environment
        ./VSView env pip list

        # Uninstall a package
        ./VSView env pip uninstall <package_name>
        ```

#### `vsview env python` `[ARGS]...`
:   Directly invoke the standalone executable's embedded Python interpreter.

    === "PowerShell (Windows)"

        ```powershell
        # Verify installed module in embedded Python
        .\VSView.exe env python -c "import vapoursynth; print(vapoursynth.__version__)"
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        # Verify installed module in embedded Python
        ./VSView env python -c "import vapoursynth; print(vapoursynth.__version__)"
        ```

#### `vsview env python-path`
:   Output the absolute path to the embedded Python executable powering the standalone application and exit.

    === "PowerShell (Windows)"

        ```powershell
        .\VSView.exe env python-path
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        ./VSView env python-path
        ```

#### `vsview env update`
:   Upgrades VSView to the latest version inside the standalone executable environment.

    This upgrades `vsview` and its direct dependencies in-place, leaving custom installed packages and plugins intact.

    **Options:**

    - `--pre`: Allow upgrading to pre-release and development versions.
    - `--restore` / `-r`: Wipe the environment first and perform a clean re-installation with full build extras (e.g. `vsview[all]`).

    === "PowerShell (Windows)"

        ```powershell
        # Standard update
        .\VSView.exe env update

        # Include pre-release versions
        .\VSView.exe env update --pre

        # Wipe and clean re-install with default build extras
        .\VSView.exe env update --restore
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        # Standard update
        ./VSView env update

        # Include pre-release versions
        ./VSView env update --pre

        # Wipe and clean re-install with default build extras
        ./VSView env update --restore
        ```

#### `vsview env restore`
:   Reset and reinstall the embedded Python environment back to its original clean state.

    === "PowerShell (Windows)"

        ```powershell
        .\VSView.exe env restore
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        ./VSView env restore
        ```

#### `vsview env remove`
:   Delete the standalone executable's environment directory from disk.

    === "PowerShell (Windows)"

        ```powershell
        .\VSView.exe env remove
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        ./VSView env remove
        ```

#### `vsview env cache` `[dist|pip|uv]`
:   Inspect or manage cached distribution archives, pip wheels, or uv cache assets used by the standalone executable launcher.

    Available cache targets:

    * `dist`: Cached compressed Python runtime archive.
    * `pip`: Cached `pip` wheels and package download artifacts.
    * `uv`: Cached `uv` installer binaries and resolution data.

    !!! tip "Safe Disk Cleanup"

        Removing any cached asset (`--remove` or `-r`) is completely safe and reclaims disk space without affecting your active VSView environment.
        Archives are automatically recreated or re-downloaded if needed later (e.g. during `restore`).

    Pass `-r` / `--remove` to wipe a specific cached asset:

    === "PowerShell (Windows)"

        ```powershell
        # Show Python distribution cache location
        .\VSView.exe env cache dist

        # Safely remove Python distribution archive to free space
        .\VSView.exe env cache dist --remove
        ```

    === "Bash / Zsh (macOS & Linux)"

        ```bash
        # Show Python distribution cache location
        ./VSView env cache dist

        # Safely remove Python distribution archive to free space
        ./VSView env cache dist --remove
        ```

---
## Environment Files (.env)

VSView automatically searches for and loads `.env` files on startup.

The search starts from your **current working directory** and moves up through parent directories until a `.env` file is found.

Setting `VSVIEW_NO_DOTENV` will disable .env file loading.
