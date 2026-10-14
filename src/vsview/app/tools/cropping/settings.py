from __future__ import annotations

from typing import Annotated, override

from pydantic import AfterValidator, BaseModel
from PySide6.QtWidgets import QWidget

from vsview.api import ListEdit, ListEditWidget, Spin

from .utils import CropValues


class CodeFormatListEdit(ListEdit[str]):
    @override
    def create_widget(self, parent: QWidget | None = None) -> ListEditWidget[str]:
        w = super().create_widget(parent)

        def _on_completer_activated(s: str) -> None:
            if not w.dialog_line_edit:
                return

            t, p = w.dialog_line_edit.text(), w.dialog_line_edit.cursorPosition()
            i = t.rfind("{", 0, p)
            w.dialog_line_edit.setText(f"{t[:i]}{{{s}}}{t[p:]}")
            w.dialog_line_edit.setCursorPosition(i + len(s) + 2)

        if w.completer:
            w.completer.activated.connect(_on_completer_activated)

        return w


DEFAULT_CODE_FORMAT = [
    "std.Crop({left}, {right}, {top}, {bottom})",
    "std.CropRel({left}, {right}, {top}, {bottom})",
    "std.CropAbs({width}, {height}, {left}, {top})",
]


def _validate_templates(v: list[str]) -> list[str]:
    sample = CropValues()._asdict()
    for fmt in v:
        try:
            fmt.format(**sample)
        except Exception as exc:
            raise ValueError(f"Invalid crop code format template {fmt!r}: {exc}") from exc
    return v


class GlobalSettings(BaseModel):
    mod: Annotated[
        int,
        Spin(
            label="Mod",
            min=1,
            max=64,
            tooltip="Snap crop coordinates to this modulus",
        ),
    ] = 2

    code_format: Annotated[
        list[str],
        AfterValidator(_validate_templates),
        CodeFormatListEdit(
            label="Code Format",
            value_type=str,
            default_value=DEFAULT_CODE_FORMAT,
            dialog_label_text="Enter code format",
            tooltip=(
                "The code format to use for the commands.\n\n"
                "Available variables:\n"
                "{left}, {top}, {right}, {bottom}, {width}, {height}"
            ),
        ),
    ] = DEFAULT_CODE_FORMAT
