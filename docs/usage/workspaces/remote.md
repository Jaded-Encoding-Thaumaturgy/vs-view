---
icon: lucide/monitor-up
title: Remote Workspace
description: Remote execution workspace for VapourSynth in VSView leveraging vsremote.
---

# Remote Workspace

The **Remote Workspace** enables remote script execution leveraging [vsremote](https://github.com/Ichunjo/vs-remote).
It loads a local script from the client and sends the code to a remote server for execution. The resulting video outputs are streamed back to the client for preview and inspection.

Refer to the [Workspaces Overview](workspace.md) for general workspace layout and viewer controls.

---

## Installation

=== "uv"
    ```bash title="Add Remote"
    uv add vsview-remote
    ```
=== "pip"
    ```bash title="Install Remote"
    pip install vsview-remote
    ```

---

## Usage

### 1. Start the Remote Server

The server must be started with `--allow-eval` to allow dynamic script execution:

```bash
vsremote serve --allow-eval
```

!!! danger
    Running with `--allow-eval` permits arbitrary Python code execution on the server.
    Refer to the [vsremote Security section](https://github.com/Ichunjo/vs-remote#security) for securing the connection (SSH tunneling, CurveZMQ encryption, client whitelisting).

### 2. Open Script in VSView

Open your local `.vpy` or `.py` script via **New &rarr; Workspace &rarr; Remote**.

A connection dialog will prompt for server connection details before executing the script.
It can be opened at any time using ++ctrl+alt+r++ to reconfigure connection parameters and reload the active script

---

## Configuration Settings

Plugin preferences can be adjusted in VSView settings under **Plugin &rarr; Remote**. Sensitive values (auth token, Curve secret key) are stored in encrypted plugin secrets.

---

## Authoring Script on Server (Shim)

If you author and host the script directly on the server (e.g. `vsremote serve script.vpy`), `--allow-eval` is not required.
You can connect from a standard Script or Editor workspace using a local shim:

```python title="shim.vpy"
import vsremote
import vsview


def setup_client() -> vsremote.RemoteClient:
    # Connect to the remote server
    client = vsremote.RemoteClient("tcp://127.0.0.1:5555")
    client.start()
    # Register a callback to close the client when the workspace is destroyed
    vsview.register_on_workspace_destroy(client.close)
    # Test the connection
    assert client.ping().result(timeout=10.0)
    return client


# Use the cached client to avoid creating a new client on every reload
client = vsview.get_cached("remote_client", setup_client)

# Send the reload signal to the server if the script is being reloaded
if vsview.is_reload():
    client.reload().result()

# Get the outputs from the remote script and set them as outputs in VSView
for idx, clip in client.get_outputs().items():
    name = client.get_clip_info(idx).result().name
    vsview.set_output(clip, idx, name)
```
