from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel

from vsview.api import Checkbox, Dropdown, LineEdit, SecretLineEdit, Spin

from ._metadata import AUTH_CONTEXT, AUTH_KEY, CURVE_CONTEXT, CURVE_KEY, WORKSPACE_ID


class GlobalSettings(BaseModel):
    address: Annotated[str, LineEdit(label="Address", tooltip="Remote server address")] = "tcp://127.0.0.1:5555"
    auth_token: Annotated[
        str | None,
        SecretLineEdit(
            label="Auth Token",
            namespace=WORKSPACE_ID,
            context=AUTH_CONTEXT,
            key=AUTH_KEY,
            placeholder_text="Optional authentication token",
            tooltip="Optional authentication token for the remote server",
        ),
    ] = None
    compression: Annotated[
        Literal["zstd", "none"],
        Dropdown(
            label="Compression",
            items=[("zstd", "zstd"), ("none", "none")],
            tooltip="Plane compression algorithm used for network frame transport",
        ),
    ] = "zstd"
    prefetch: Annotated[
        int,
        Spin(
            label="Prefetch Frames",
            min=0,
            max=64,
            tooltip="Number of frames to prefetch asynchronously ahead of time (0 to disable)",
        ),
    ] = 4
    backlog: Annotated[
        int | None,
        Spin(
            label="Backlog",
            min=1,
            max=128,
            tooltip="Maximum number of in-flight and prefetched frame requests buffered",
        ),
    ] = None
    forward_logs: Annotated[
        bool,
        Checkbox(
            label="Forward Logs",
            text="Forward remote server logs",
            tooltip="Dispatch remote LogRecords to the client logging system",
        ),
    ] = True
    subscribe_streams: Annotated[
        bool,
        Checkbox(
            label="Subscribe Streams",
            text="Subscribe to stdout/stderr",
            tooltip="Stream stdout and stderr output from the remote server",
        ),
    ] = True
    use_curve: Annotated[
        bool,
        Checkbox(
            label="CurveZMQ Encryption",
            text="Enable CurveZMQ encryption",
            tooltip="Enable CurveZMQ end-to-end encryption for the connection",
        ),
    ] = False
    curve_server_key: Annotated[
        str | None,
        LineEdit(label="Curve Server Public Key", tooltip="Server public key for CurveZMQ encryption"),
    ] = None
    curve_public_key: Annotated[
        str | None,
        LineEdit(label="Curve Client Public Key", tooltip="Client public key for CurveZMQ authentication"),
    ] = None
    curve_secret_key: Annotated[
        str | None,
        SecretLineEdit(
            label="Curve Client Secret Key",
            namespace=WORKSPACE_ID,
            context=CURVE_CONTEXT,
            key=CURVE_KEY,
            placeholder_text="Client secret key (optional)",
            tooltip="Client secret key for CurveZMQ encryption",
        ),
    ] = None
