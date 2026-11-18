import ast
import inspect
from collections.abc import Callable
from textwrap import dedent
from typing import Any


def script_source(func: Callable[..., object], /, **params: Any) -> str:
    """Return the body of `func` as module source, with `params` bound as globals."""
    signature = inspect.signature(func)
    literals = dict[str, Any]()

    for name, param in signature.parameters.items():
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            if name in params:
                raise TypeError(f"{func.__name__}: {name!r} is provided at run time and cannot be bound")
            continue
        if param.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
            raise TypeError(f"{func.__name__}: parameter {name!r} must be a plain or keyword-only parameter")
        if name in params:
            literals[name] = params.pop(name)
        elif param.default is not inspect.Parameter.empty:
            literals[name] = param.default
        else:
            raise TypeError(f"{func.__name__}: missing value for {name!r}")

    if params:
        raise TypeError(f"{func.__name__}: unexpected parameters {sorted(params)}")

    header = ""
    for name, value in literals.items():
        # The value is written as its repr, so it must read back as the same value.
        if ast.literal_eval(repr(value)) != value:
            raise ValueError(f"{func.__name__}: {name!r} = {value!r} is not a literal")
        header += f"{name} = {value!r}\n"

    return header + _body_source(func)


def _body_source(func: Callable[..., object]) -> str:
    source = dedent(inspect.getsource(func))
    func_def = ast.parse(source).body[0]
    if not isinstance(func_def, ast.FunctionDef):
        raise TypeError(f"{func.__name__} is not defined with a def statement")

    body = func_def.body
    if ast.get_docstring(func_def) is not None:
        body = body[1:]
    if not body:
        raise ValueError(f"{func.__name__} has no statements besides its docstring")

    # A decorated statement starts at its first decorator, not at its `def` line.
    first = body[0]
    decorators = first.decorator_list if isinstance(first, ast.FunctionDef | ast.ClassDef) else []
    start = min([first.lineno, *(decorator.lineno for decorator in decorators)])

    lines = source.splitlines(keepends=True)
    return dedent("".join(lines[start - 1 : func_def.end_lineno]))


def blank_clip_script(length: int = 100) -> None:
    """One red 64x64 RGB24 output at 24 fps."""
    import vapoursynth as vs

    clip = vs.core.std.BlankClip(
        length=length, width=64, height=64, format=vs.RGB24, fpsnum=24, fpsden=1, color=[255, 0, 0]
    )
    clip = vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )
    clip.set_output(0)


def dual_output_script(length: int = 100) -> None:
    """Two 64x64 RGB24 outputs at 24 fps: red at index 0 and green at index 1."""
    import vapoursynth as vs

    for index, color in enumerate(([255, 0, 0], [0, 255, 0])):
        clip = vs.core.std.BlankClip(
            length=length, width=64, height=64, format=vs.RGB24, fpsnum=24, fpsden=1, color=color
        )
        clip = vs.core.std.SetFrameProps(
            clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
        )
        clip.set_output(index)


def audio_tracks_script(num_tracks: int) -> None:
    """One video output at index 0, then `num_tracks` audio outputs from index 1."""
    import vapoursynth as vs

    clip = vs.core.std.BlankClip(length=100, width=64, height=64, format=vs.RGB24, fpsnum=24, fpsden=1)
    clip = vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )
    clip.set_output(0)

    for index in range(1, num_tracks + 1):
        vs.core.std.BlankAudio(length=48000, samplerate=48000).set_output(index)
