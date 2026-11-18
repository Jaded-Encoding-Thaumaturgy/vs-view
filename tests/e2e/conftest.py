from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.scripts import script_source


def _rgb_splice_script(length: int) -> None:
    import vapoursynth as vs

    clip_r = vs.core.std.BlankClip(length=24, width=64, height=64, format=vs.RGB24, color=[255, 0, 0])
    clip_g = vs.core.std.BlankClip(length=24, width=64, height=64, format=vs.RGB24, color=[0, 255, 0])
    clip_b = vs.core.std.BlankClip(length=max(1, length - 48), width=64, height=64, format=vs.RGB24, color=[0, 0, 255])

    clip = vs.core.std.Splice([clip_r, clip_g, clip_b], mismatch=True)
    clip = vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )

    clip.set_output(0)


def _dual_size_script(length: int) -> None:
    import vapoursynth as vs

    clip0 = vs.core.std.BlankClip(length=length, width=64, height=64, format=vs.RGB24, color=[255, 0, 0])
    clip0 = vs.core.std.SetFrameProps(
        clip0, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )

    clip1 = vs.core.std.BlankClip(length=length * 2, width=128, height=128, format=vs.RGB24, color=[0, 255, 0])
    clip1 = vs.core.std.SetFrameProps(
        clip1, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )

    clip0.set_output(0)
    clip1.set_output(1)


@pytest.fixture
def vpy_script_factory(tmp_path: Path) -> Callable[..., Path]:
    """Factory fixture generating isolated VapourSynth test scripts in tmp_path."""
    counter = 0

    def create_script(*, length: int = 100, filename: str | None = None, dual_output: bool = False) -> Path:
        nonlocal counter
        counter += 1
        target = tmp_path / (filename or f"script_{counter}.vpy")
        script = _dual_size_script if dual_output else _rgb_splice_script
        target.write_text(script_source(script, length=length), encoding="utf-8")
        return target

    return create_script
