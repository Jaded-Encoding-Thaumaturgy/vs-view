---
icon: lucide/file-code
---

# Quick Script

The **Quick Script** workspace is used for testing and experiments. It provides an environment to write and execute VapourSynth code without creating a dedicated script file.

Refer to the [Workspaces Overview](workspace.md) for general workspace layout and viewer controls.

## Integrated Code Editor

The editor is a **dockable widget**, which can be moved, floated, or docked to different sides of the workspace.

### Toolbar Actions

- :lucide-play: **Run script** (++f5++ or ++ctrl+r++): Executes the code. If the script is already running, it triggers a reload.
- :lucide-save: **Save script to file**: Exports the current code to a `.vpy` file.

### Themes & Customization

The editor's appearance is configurable in the **Appearance** section of the settings:

- **Editor Themes**: Sourced directly from **Pygments**. The available styles can be expanded by adding more Pygments styles to the environment.
- **UI Elements**: Syntax highlighting, line numbers, and the active line indicator adjust according to the selected theme.

### Key Shortcuts

| Action           | Shortcut             | Description            |
| :--------------- | :------------------- | :--------------------- |
| **Run / Reload** | ++f5++ or ++ctrl+r++ | Executes the code.     |
| **Zoom In/Out**  | ++ctrl++ + **Wheel** | Adjusts the font size. |

