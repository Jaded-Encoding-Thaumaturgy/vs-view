---
icon: lucide/file-pen
title: Visual Studio Code Integration
description: Run VSView on the current file directly from Visual Studio Code.
---

# Visual Studio Code Integration

You can set up a Visual Studio Code task to launch VSView on the file you are
currently editing. This is useful for quickly previewing VapourSynth scripts.

## Task configuration

Create (or extend) `.vscode/tasks.json` in your project:

```json title=".vscode/tasks.json"
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "VSView",
      "type": "shell",
      "command": {
        "value": "${command:python.interpreterPath}",
        "quoting": "strong"
      },
      "args": [
        "-m",
        "vsview",
        { "value": "${file}", "quoting": "strong" }
      ],
      "group": {
        "kind": "none",
        "isDefault": true
      },
      "presentation": {
        "reveal": "always",
        "panel": "shared"
      }
    }
  ]
}
```

`${command:python.interpreterPath}` resolves to the Python interpreter
currently selected in Visual Studio Code (via the
[Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python)),
so the task works regardless of where the virtual environment lives or
which platform you are on. Make sure VSView is installed in that
interpreter's environment.

With your script open, run the task via **Terminal → Run Task… → VSView**,
or bind it to a shortcut by adding a keybinding for
`workbench.action.tasks.runTask` with `"args": "VSView"`.

!!! warning "Avoid `launch.json` / debugpy"

    Prefer a task over a debug launch configuration (`type: "debugpy"` in
    `launch.json`). Running VSView under the debugger can degrade performance
    and cause deadlocks, especially when breakpoints are set.
