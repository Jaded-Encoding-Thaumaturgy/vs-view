from __future__ import annotations

from typing import Literal

import pytest
import vapoursynth as vs

from vspackrgb import helpers, rust

MAIN_SIZE = (64, 48)

FORMATS = [
    pytest.param(vs.RGB24, vs.GRAY8, "pack_bgra_8bit", id="RGB24"),
    pytest.param(vs.RGB30, vs.GRAY10, "pack_rgb30_10bit", id="RGB30"),
    pytest.param(vs.RGB48, vs.GRAY16, "pack_rgba64_16bit", id="RGB48"),
    pytest.param(vs.RGBH, vs.GRAYH, "pack_rgba16f_16bit", id="RGBH"),
    pytest.param(vs.RGBS, vs.GRAYS, "pack_rgba32f_32bit", id="RGBS"),
]

ALPHA_SIZES = [
    pytest.param((16, 16), id="smaller"),
    pytest.param((128, 96), id="larger"),
    pytest.param((32, 48), id="narrower"),
    pytest.param((64, 24), id="shorter"),
    pytest.param((64, 49), id="one-row-taller"),
]

pytestmark = [
    pytest.mark.vpy("initial-core"),
    pytest.mark.parametrize(("main_format", "alpha_format", "packer_name"), FORMATS),
]


@pytest.fixture
def native_calls(monkeypatch: pytest.MonkeyPatch, packer_name: str) -> list[tuple[object, ...]]:
    # A mismatched alpha makes the real packer read out of bounds, so it must never run here.
    calls = list[tuple[object, ...]]()

    def record(*args: object) -> None:
        calls.append(args)

    monkeypatch.setattr(rust, packer_name, record)
    return calls


def _clips(
    main_format: vs.PresetVideoFormat, alpha_format: vs.PresetVideoFormat, alpha_size: tuple[int, int]
) -> tuple[vs.VideoNode, vs.VideoNode]:
    main = vs.core.std.BlankClip(format=main_format, width=MAIN_SIZE[0], height=MAIN_SIZE[1], length=1)
    alpha = vs.core.std.BlankClip(format=alpha_format, width=alpha_size[0], height=alpha_size[1], length=1)
    return main, alpha


def _match(alpha_size: tuple[int, int]) -> str:
    return rf"Alpha dimensions must match the clip \(64x48\), got {alpha_size[0]}x{alpha_size[1]}"


@pytest.mark.parametrize("alpha_size", ALPHA_SIZES)
@pytest.mark.parametrize("alpha_mode", ["explicit", "prop"])
def test_packrgb_node_rejects_alpha_of_another_size(
    main_format: vs.PresetVideoFormat,
    alpha_format: vs.PresetVideoFormat,
    alpha_size: tuple[int, int],
    alpha_mode: Literal["explicit", "prop"],
    native_calls: list[tuple[object, ...]],
) -> None:
    main, alpha = _clips(main_format, alpha_format, alpha_size)
    src = main if alpha_mode == "explicit" else main.std.ClipToProp(alpha, prop="_Alpha")
    alpha_arg: vs.VideoNode | Literal[True] = alpha if alpha_mode == "explicit" else True

    with pytest.raises(ValueError, match=_match(alpha_size)):
        helpers.packrgb(src, alpha_arg, "rust")

    assert native_calls == []


@pytest.mark.parametrize("alpha_size", ALPHA_SIZES)
@pytest.mark.parametrize("alpha_mode", ["explicit", "prop"])
def test_packrgb_frame_rejects_alpha_of_another_size(
    main_format: vs.PresetVideoFormat,
    alpha_format: vs.PresetVideoFormat,
    alpha_size: tuple[int, int],
    alpha_mode: Literal["explicit", "prop"],
    native_calls: list[tuple[object, ...]],
) -> None:
    main, alpha = _clips(main_format, alpha_format, alpha_size)
    src = main if alpha_mode == "explicit" else main.std.ClipToProp(alpha, prop="_Alpha")

    with src.get_frame(0) as src_frame, alpha.get_frame(0) as alpha_frame:
        alpha_arg: vs.VideoFrame | Literal[True] = alpha_frame if alpha_mode == "explicit" else True

        with pytest.raises(ValueError, match=_match(alpha_size)):
            helpers.packrgb(src_frame, alpha_arg, "rust")

    assert native_calls == []


def test_packrgb_accepts_alpha_of_the_same_size(
    main_format: vs.PresetVideoFormat,
    alpha_format: vs.PresetVideoFormat,
    native_calls: list[tuple[object, ...]],
) -> None:
    main, alpha = _clips(main_format, alpha_format, MAIN_SIZE)

    with main.get_frame(0) as main_frame, alpha.get_frame(0) as alpha_frame:
        packed = helpers.packrgb(main_frame, alpha_frame, "rust")

    assert isinstance(packed, vs.VideoFrame)
    assert len(native_calls) == 1
    assert isinstance(native_calls[0][3], int)
    assert native_calls[0][4:6] == MAIN_SIZE
