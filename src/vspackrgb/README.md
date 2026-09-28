# VSPackRGB

RGB packing for VapourSynth frames.

Converts planar RGB VapourSynth clips into display-ready packed formats:

- **RGB24 → BGRA** (8-bit interleaved, stored in `GRAY32`)
- **RGB30 → A2R10G10B10** (10-bit packed, stored in `GRAY32`)
- **RGB48 → RGBA64** (16-bit interleaved, stored in `GRAY16`)
- **RGBH → RGBA16F** (16-bit float interleaved, stored in `GRAYH`)
- **RGBS → RGBA32F** (32-bit float interleaved, stored in `GRAYS`)

For higher-than-10-bit formats, the output clip is 4x wider than the input to accommodate the interleaved R, G, B, and A channels.

## Installation

Prebuilt wheels are provided for most platforms. If a compatible wheel is available, no compilation is required.

```bash
pip install vspackrgb
```

With `uv`:

```bash
uv add vspackrgb
```

## Benchmarks

- CPU 9800X3D Windows 11 Pro 25H2 (26200.8655)
- Python 3.12.14
- VapourSynth R80 (With unlimited `max_cache_size`)
- vszip 22.1.0
- libp2p R2 (+ RGB48 packing fix)
- akarin 1.5.0
- Cargo & rustc 1.98.0
- numpy 2.5.3
- numba 0.67.0

### Blank clip with `keep=True`

```
             RGB24 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ vszip.PackRGB      │  20000 │  4.608s │ 4339.86 │
│ libp2p.Pack        │  20000 │  4.519s │ 4426.02 │
│ akarin.Expr        │  20000 │  4.677s │ 4276.20 │
│ vspackrgb (rust)   │   7000 │  6.433s │ 1088.12 │
│ vspackrgb (numba)  │   7000 │  2.439s │ 2870.36 │
│ vspackrgb (numpy)  │   2000 │  5.816s │  343.88 │
│ vspackrgb (python) │     25 │ 10.053s │    2.49 │
└────────────────────┴────────┴─────────┴─────────┘

            RGB30 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃   Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ vszip.PackRGB      │  20000 │ 4.689s │ 4265.13 │
│ libp2p.Pack        │  20000 │ 4.798s │ 4168.77 │
│ akarin.Expr        │  20000 │ 4.708s │ 4248.45 │
│ vspackrgb (rust)   │   7000 │ 2.742s │ 2553.35 │
│ vspackrgb (numba)  │   7000 │ 2.539s │ 2757.50 │
│ vspackrgb (numpy)  │   2000 │ 9.144s │  218.73 │
│ vspackrgb (python) │     25 │ 7.104s │    3.52 │
└────────────────────┴────────┴────────┴─────────┘

             RGB48 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ libp2p.Pack        │  20000 │ 10.957s │ 1825.31 │
│ vspackrgb (rust)   │   7000 │  5.161s │ 1356.34 │
│ vspackrgb (numba)  │   7000 │  5.150s │ 1359.27 │
│ vspackrgb (numpy)  │   2000 │  8.131s │  245.96 │
│ vspackrgb (python) │     25 │  9.989s │    2.50 │
└────────────────────┴────────┴─────────┴─────────┘

             RGBH Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃   Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 5.204s │ 1345.05 │
│ vspackrgb (numba)  │   7000 │ 5.112s │ 1369.27 │
│ vspackrgb (numpy)  │   2000 │ 8.168s │  244.86 │
│ vspackrgb (python) │     25 │ 9.928s │    2.52 │
└────────────────────┴────────┴────────┴─────────┘

             RGBS Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 11.708s │ 597.88 │
│ vspackrgb (numba)  │   7000 │ 10.171s │ 688.23 │
│ vspackrgb (numpy)  │   2000 │ 13.968s │ 143.18 │
│ vspackrgb (python) │     25 │  9.945s │   2.51 │
└────────────────────┴────────┴─────────┴────────┘
```

### Real world scenario

Source clip is a 1080p `.m2ts` file muxed to `.mkv`,
indexed with BestSource R18 and resampled to the target format with `resize.Point`

```
            RGB24 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vszip.PackRGB      │   7000 │  9.078s │ 771.06 │
│ libp2p.Pack        │   7000 │  8.980s │ 779.49 │
│ akarin.Expr        │   7000 │  9.400s │ 744.65 │
│ vspackrgb (rust)   │   7000 │  9.794s │ 714.75 │
│ vspackrgb (numba)  │   7000 │  9.067s │ 772.02 │
│ vspackrgb (numpy)  │   2000 │  6.720s │ 297.60 │
│ vspackrgb (python) │     25 │ 10.004s │   2.50 │
└────────────────────┴────────┴─────────┴────────┘

            RGB30 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vszip.PackRGB      │   7000 │  9.581s │ 730.63 │
│ libp2p.Pack        │   7000 │  9.908s │ 706.53 │
│ akarin.Expr        │   7000 │ 10.317s │ 678.52 │
│ vspackrgb (rust)   │   7000 │  9.699s │ 721.70 │
│ vspackrgb (numba)  │   7000 │  9.658s │ 724.82 │
│ vspackrgb (numpy)  │   2000 │ 10.550s │ 189.58 │
│ vspackrgb (python) │     25 │  7.134s │   3.50 │
└────────────────────┴────────┴─────────┴────────┘

            RGB48 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ libp2p.Pack        │   7000 │ 10.607s │ 659.97 │
│ vspackrgb (rust)   │   7000 │ 11.426s │ 612.61 │
│ vspackrgb (numba)  │   7000 │ 11.333s │ 617.67 │
│ vspackrgb (numpy)  │   2000 │  9.188s │ 217.67 │
│ vspackrgb (python) │     25 │ 10.006s │   2.50 │
└────────────────────┴────────┴─────────┴────────┘

             RGBH Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 11.462s │ 610.72 │
│ vspackrgb (numba)  │   7000 │ 11.335s │ 617.54 │
│ vspackrgb (numpy)  │   2000 │  9.460s │ 211.42 │
│ vspackrgb (python) │     25 │  9.980s │   2.50 │
└────────────────────┴────────┴─────────┴────────┘

             RGBS Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 20.458s │ 342.16 │
│ vspackrgb (numba)  │   7000 │ 19.119s │ 366.14 │
│ vspackrgb (numpy)  │   2000 │ 14.731s │ 135.77 │
│ vspackrgb (python) │     25 │  9.924s │   2.52 │
└────────────────────┴────────┴─────────┴────────┘
```

## Building

You only need a working C compiler/toolchain for your platform:

- Windows: Visual Studio Build Tools (Desktop development with C++)
- Linux: GCC/Clang and Python headers
- macOS: Xcode Command Line Tools

```bash
uv build --sdist --wheel --verbose
```
