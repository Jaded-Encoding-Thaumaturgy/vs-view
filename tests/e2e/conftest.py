from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def vpy_script_factory(tmp_path: Path) -> Callable[..., Path]:
    """Factory fixture generating isolated VapourSynth test scripts in tmp_path."""
    counter = 0

    def create_script(
        *,
        content: str | None = None,
        length: int = 100,
        filename: str | None = None,
        dual_output: bool = False,
    ) -> Path:
        nonlocal counter
        counter += 1
        name = filename or f"script_{counter}.vpy"
        target = tmp_path / name

        if content is not None:
            target.write_text(dedent(content).strip() + "\n", encoding="utf-8")
            return target

        if dual_output:
            script_body = dedent(f"""\
                import vapoursynth as vs

                core = vs.core

                clip0 = core.std.BlankClip(length={length}, width=64, height=64, format=vs.RGB24, color=[255, 0, 0])
                clip0 = core.std.SetFrameProps(
                    clip0, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
                )

                clip1 = core.std.BlankClip(
                    length={length * 2}, width=128, height=128, format=vs.RGB24, color=[0, 255, 0]
                )
                clip1 = core.std.SetFrameProps(
                    clip1, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
                )

                clip0.set_output(0)
                clip1.set_output(1)
            """)
        else:
            blue_len = max(1, length - 48)
            script_body = dedent(f"""\
                import vapoursynth as vs

                core = vs.core

                clip_r = core.std.BlankClip(length=24, width=64, height=64, format=vs.RGB24, color=[255, 0, 0])
                clip_g = core.std.BlankClip(length=24, width=64, height=64, format=vs.RGB24, color=[0, 255, 0])
                clip_b = core.std.BlankClip(length={blue_len}, width=64, height=64, format=vs.RGB24, color=[0, 0, 255])

                clip = core.std.Splice([clip_r, clip_g, clip_b], mismatch=True)
                clip = core.std.SetFrameProps(
                    clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
                )

                clip.set_output(0)
            """)

        target.write_text(script_body, encoding="utf-8")
        return target

    return create_script
