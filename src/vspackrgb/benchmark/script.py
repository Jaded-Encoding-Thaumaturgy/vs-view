from collections import deque
from time import perf_counter

import vapoursynth as vs
from rich.console import Console
from rich.table import Table

from vspackrgb import packrgb

WIDTH = 1920
HEIGHT = 1080


FRAME_COUNTS = {
    "vszip.PackRGB": 20000,
    "libp2p.Pack": 20000,
    "akarin.Expr": 20000,
    "vspackrgb (rust)": 7000,
    "vspackrgb (numba)": 7000,
    "vspackrgb (numpy)": 2000,
    "vspackrgb (python)": 25,
}

console = Console(stderr=True)


def benchmark(name: str, clip: vs.VideoNode, num_frames: int) -> tuple[float, float]:
    with console.status(f"[bold cyan]Benchmarking {name}..."):
        start = perf_counter()
        deque(clip.frames(), maxlen=0)
        elapsed = perf_counter() - start
        fps = num_frames / elapsed
    return elapsed, fps


def get_clip(num_frames: int, fmt: int) -> vs.VideoNode:
    return vs.core.std.BlankClip(width=WIDTH, height=HEIGHT, format=fmt, length=num_frames, keep=True)


def benchmark_rgb24() -> Table:
    table = Table(title=f"RGB24 Packing ({WIDTH}x{HEIGHT})")
    table.add_column("Backend", style="cyan", no_wrap=True)
    table.add_column("Frames", justify="right", style="magenta")
    table.add_column("Time", justify="right", style="yellow")
    table.add_column("FPS", justify="right", style="green")

    # vszip.PackRGB
    name = "vszip.PackRGB"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = clip.vszip.PackRGB()
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # libp2p.Pack
    name = "libp2p.Pack"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = clip.libp2p.Pack()
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # akarin.Expr
    name = "akarin.Expr"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = vs.core.akarin.Expr(clip.std.SplitPlanes(), "x 0x10000 * y 0x100 * + z + 0xff000000 +", vs.GRAY32, opt=1)
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - cython
    name = "vspackrgb (rust)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = packrgb(clip, backend="rust")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numba
    name = "vspackrgb (numba)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)

    # Warm up JIT
    packrgb(get_clip(1, vs.RGB24), backend="numba").get_frame(0).close()

    packed = packrgb(clip, backend="numba")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numpy
    name = "vspackrgb (numpy)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = packrgb(clip, backend="numpy")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - python
    name = "vspackrgb (python)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB24)
    packed = packrgb(clip, backend="python")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    console.print(table)
    return table


def benchmark_rgb30() -> Table:
    table = Table(title=f"RGB30 Packing ({WIDTH}x{HEIGHT})")
    table.add_column("Backend", style="cyan", no_wrap=True)
    table.add_column("Frames", justify="right", style="magenta")
    table.add_column("Time", justify="right", style="yellow")
    table.add_column("FPS", justify="right", style="green")

    # vszip.PackRGB
    name = "vszip.PackRGB"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = clip.vszip.PackRGB()
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # libp2p.Pack
    name = "libp2p.Pack"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = clip.libp2p.Pack()
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # akarin.Expr
    name = "akarin.Expr"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = vs.core.akarin.Expr(clip.std.SplitPlanes(), "x 0x100000 * y 0x400 * + z + 0xc0000000 +", vs.GRAY32, opt=1)
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - cython
    name = "vspackrgb (rust)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = packrgb(clip, backend="rust")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numba
    name = "vspackrgb (numba)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)

    # Warm up JIT
    packrgb(get_clip(1, vs.RGB30), backend="numba").get_frame(0).close()

    packed = packrgb(clip, backend="numba")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numpy
    name = "vspackrgb (numpy)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = packrgb(clip, backend="numpy")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - python
    name = "vspackrgb (python)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB30)
    packed = packrgb(clip, backend="python")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    console.print(table)
    return table


def benchmark_rgb48() -> Table:
    table = Table(title=f"RGB48 Packing ({WIDTH}x{HEIGHT})")
    table.add_column("Backend", style="cyan", no_wrap=True)
    table.add_column("Frames", justify="right", style="magenta")
    table.add_column("Time", justify="right", style="yellow")
    table.add_column("FPS", justify="right", style="green")

    # libp2p.Pack
    name = "libp2p.Pack"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB48)
    packed = clip.libp2p.Pack()
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - cython
    name = "vspackrgb (rust)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB48)
    packed = packrgb(clip, backend="rust")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numba
    name = "vspackrgb (numba)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB48)

    # Warm up JIT
    packrgb(get_clip(1, vs.RGB48), backend="numba").get_frame(0).close()

    packed = packrgb(clip, backend="numba")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numpy
    name = "vspackrgb (numpy)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB48)
    packed = packrgb(clip, backend="numpy")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - python
    name = "vspackrgb (python)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGB48)
    packed = packrgb(clip, backend="python")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    console.print(table)
    return table


def benchmark_rgbh() -> Table:
    table = Table(title=f"RGBH Packing ({WIDTH}x{HEIGHT})")
    table.add_column("Backend", style="cyan", no_wrap=True)
    table.add_column("Frames", justify="right", style="magenta")
    table.add_column("Time", justify="right", style="yellow")
    table.add_column("FPS", justify="right", style="green")

    # vspackrgb - cython
    name = "vspackrgb (rust)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBH)
    packed = packrgb(clip, backend="rust")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numba
    name = "vspackrgb (numba)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBH)

    # Warm up JIT
    packrgb(get_clip(1, vs.RGBH), backend="numba").get_frame(0).close()

    packed = packrgb(clip, backend="numba")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numpy
    name = "vspackrgb (numpy)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBH)
    packed = packrgb(clip, backend="numpy")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - python
    name = "vspackrgb (python)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBH)
    packed = packrgb(clip, backend="python")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    console.print(table)
    return table


def benchmark_rgbs() -> Table:
    table = Table(title=f"RGBS Packing ({WIDTH}x{HEIGHT})")
    table.add_column("Backend", style="cyan", no_wrap=True)
    table.add_column("Frames", justify="right", style="magenta")
    table.add_column("Time", justify="right", style="yellow")
    table.add_column("FPS", justify="right", style="green")

    # vspackrgb - cython
    name = "vspackrgb (rust)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBS)
    packed = packrgb(clip, backend="rust")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numba
    name = "vspackrgb (numba)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBS)

    # Warm up JIT
    packrgb(get_clip(1, vs.RGBS), backend="numba").get_frame(0).close()

    packed = packrgb(clip, backend="numba")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - numpy
    name = "vspackrgb (numpy)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBS)
    packed = packrgb(clip, backend="numpy")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    # vspackrgb - python
    name = "vspackrgb (python)"
    num_frames = FRAME_COUNTS[name]
    clip = get_clip(num_frames, vs.RGBS)
    packed = packrgb(clip, backend="python")
    elapsed, fps = benchmark(name, packed, num_frames)
    table.add_row(name, str(num_frames), f"{elapsed:.3f}s", f"{fps:.2f}")

    console.print(table)
    return table


def main() -> None:
    console.print()
    table_rgb24 = benchmark_rgb24()  # noqa: F841
    console.print()
    table_rgb30 = benchmark_rgb30()  # noqa: F841
    console.print()
    table_rgb48 = benchmark_rgb48()  # noqa: F841
    console.print()
    table_rgbh = benchmark_rgbh()  # noqa: F841
    console.print()
    table_rgbs = benchmark_rgbs()  # noqa: F841
    console.print()


if __name__ == "__main__":
    main()
