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

- CPU 9800X3D Windows 11 Pro 25H2 (26200.9457)
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
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃   Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ vszip.PackRGB      │  20000 │ 4.584s │ 4363.21 │
│ libp2p.Pack        │  20000 │ 4.607s │ 4341.24 │
│ akarin.Expr        │  20000 │ 4.585s │ 4362.14 │
│ vspackrgb (rust)   │  20000 │ 6.069s │ 3295.65 │
│ vspackrgb (numba)  │  20000 │ 6.517s │ 3068.98 │
│ vspackrgb (numpy)  │   2000 │ 5.857s │  341.45 │
│ vspackrgb (python) │     25 │ 9.935s │    2.52 │
└────────────────────┴────────┴────────┴─────────┘

            RGB30 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃   Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ vszip.PackRGB      │  20000 │ 4.764s │ 4198.59 │
│ libp2p.Pack        │  20000 │ 4.833s │ 4138.52 │
│ akarin.Expr        │  20000 │ 4.754s │ 4207.35 │
│ vspackrgb (rust)   │  20000 │ 6.529s │ 3063.45 │
│ vspackrgb (numba)  │  20000 │ 6.862s │ 2914.50 │
│ vspackrgb (numpy)  │   2000 │ 9.029s │  221.52 │
│ vspackrgb (python) │     25 │ 7.074s │    3.53 │
└────────────────────┴────────┴────────┴─────────┘

             RGB48 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ libp2p.Pack        │  20000 │ 10.946s │ 1827.08 │
│ vspackrgb (rust)   │  20000 │ 11.751s │ 1701.98 │
│ vspackrgb (numba)  │  20000 │ 11.867s │ 1685.31 │
│ vspackrgb (numpy)  │   2000 │  8.180s │  244.51 │
│ vspackrgb (python) │     25 │  9.992s │    2.50 │
└────────────────────┴────────┴─────────┴─────────┘

             RGBH Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃     FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ vspackrgb (rust)   │  20000 │ 12.036s │ 1661.64 │
│ vspackrgb (numba)  │  20000 │ 12.106s │ 1652.04 │
│ vspackrgb (numpy)  │   2000 │  8.164s │  244.99 │
│ vspackrgb (python) │     25 │ 10.032s │    2.49 │
└────────────────────┴────────┴─────────┴─────────┘

             RGBS Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │  20000 │ 22.387s │ 893.37 │
│ vspackrgb (numba)  │  20000 │ 23.566s │ 848.69 │
│ vspackrgb (numpy)  │   2000 │ 12.055s │ 165.91 │
│ vspackrgb (python) │     25 │  9.886s │   2.53 │
└────────────────────┴────────┴─────────┴────────┘
```

### Real world scenario

Source clip is a 1080p `.m2ts` file muxed to `.mkv`,
indexed with BestSource R22 and resampled to the target format with `resize.Point`

```
            RGB24 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vszip.PackRGB      │   7000 │  9.087s │ 770.35 │
│ libp2p.Pack        │   7000 │  9.054s │ 773.16 │
│ akarin.Expr        │   7000 │  9.505s │ 736.49 │
│ vspackrgb (rust)   │   7000 │  9.132s │ 766.57 │
│ vspackrgb (numba)  │   7000 │  9.223s │ 758.99 │
│ vspackrgb (numpy)  │   2000 │  6.928s │ 288.67 │
│ vspackrgb (python) │     25 │ 10.039s │   2.49 │
└────────────────────┴────────┴─────────┴────────┘

            RGB30 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vszip.PackRGB      │   7000 │  9.617s │ 727.86 │
│ libp2p.Pack        │   7000 │  9.970s │ 702.10 │
│ akarin.Expr        │   7000 │ 10.620s │ 659.12 │
│ vspackrgb (rust)   │   7000 │  9.664s │ 724.37 │
│ vspackrgb (numba)  │   7000 │  9.719s │ 720.28 │
│ vspackrgb (numpy)  │   2000 │ 10.625s │ 188.24 │
│ vspackrgb (python) │     25 │  7.199s │   3.47 │
└────────────────────┴────────┴─────────┴────────┘

            RGB48 Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ libp2p.Pack        │   7000 │ 11.134s │ 628.70 │
│ vspackrgb (rust)   │   7000 │ 11.443s │ 611.71 │
│ vspackrgb (numba)  │   7000 │ 11.547s │ 606.22 │
│ vspackrgb (numpy)  │   2000 │  9.501s │ 210.50 │
│ vspackrgb (python) │     25 │  9.972s │   2.51 │
└────────────────────┴────────┴─────────┴────────┘

             RGBH Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 11.413s │ 613.35 │
│ vspackrgb (numba)  │   7000 │ 11.525s │ 607.39 │
│ vspackrgb (numpy)  │   2000 │  8.886s │ 225.06 │
│ vspackrgb (python) │     25 │ 10.028s │   2.49 │
└────────────────────┴────────┴─────────┴────────┘

             RGBS Packing (1920x1080)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Backend            ┃ Frames ┃    Time ┃    FPS ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ vspackrgb (rust)   │   7000 │ 19.384s │ 361.13 │
│ vspackrgb (numba)  │   7000 │ 19.118s │ 366.15 │
│ vspackrgb (numpy)  │   2000 │ 13.894s │ 143.94 │
│ vspackrgb (python) │     25 │  9.947s │   2.51 │
└────────────────────┴────────┴─────────┴────────┘
```

## Building

### Requirements

- [Cargo](https://doc.rust-lang.org/cargo/getting-started/installation.html)

```bash
uv build --sdist --wheel --verbose
```
