from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from vsview.api import Checkbox, Dropdown, LineEdit, LocalSettingsModel, Spin

from .stubs import get_stubs_dir


class Theme(StrEnum):
    DARK_MODERN = "Dark Modern", "dark"
    LIGHT_MODERN = "Light Modern", "light"
    DARK_PLUS = "Dark+", "dark"
    LIGHT_PLUS = "Light+", "light"
    DARK_2026 = "Dark 2026", "dark"
    LIGHT_2026 = "Light 2026", "light"

    HIGH_CONTRAST_DARK = "Default High Contrast", "hc-dark"
    HIGH_CONTRAST_LIGHT = "Default High Contrast Light", "hc-light"

    VISUAL_STUDIO_DARK = "Visual Studio Dark", "dark"
    VISUAL_STUDIO_LIGHT = "Visual Studio Light", "light"

    GITHUB_DARK_DEFAULT = "GitHub Dark Default", "dark"
    GITHUB_DARK = "GitHub Dark", "dark"
    GITHUB_DARK_COLORBLIND = "GitHub Dark Colorblind", "dark"
    GITHUB_DARK_DIMMED = "GitHub Dark Dimmed", "dark"
    GITHUB_DARK_HIGH_CONTRAST = "GitHub Dark High Contrast", "hc-dark"

    GITHUB_LIGHT_DEFAULT = "GitHub Light Default", "light"
    GITHUB_LIGHT = "GitHub Light", "light"
    GITHUB_LIGHT_COLORBLIND = "GitHub Light Colorblind", "light"
    GITHUB_LIGHT_DIMMED = "GitHub Light Dimmed", "light"
    GITHUB_LIGHT_HIGH_CONTRAST = "GitHub Light High Contrast", "hc-light"

    kind: str

    def __new__(cls, value: str, kind: Literal["dark", "light", "hc-dark", "hc-light"]) -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.kind = kind
        return obj


class BasedpyrightSettings(BaseModel):
    __section__ = "BasedPyright"
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    type_checking_mode: Annotated[
        str,
        Dropdown(
            label="Type Checking Mode",
            items=[
                ("Off", "off"),
                ("Basic", "basic"),
                ("Standard", "standard"),
                ("Strict", "strict"),
                ("Recommended", "recommended"),
                ("All", "all"),
            ],
            tooltip="Analysis mode for Basedpyright type checking",
        ),
    ] = Field(default="standard", alias="basedpyright.analysis.typeCheckingMode")
    disable_language_service: Annotated[
        bool,
        Checkbox(
            label="Disable Language Service",
            text="",
            tooltip="Disables the BasedPyright language service",
        ),
    ] = Field(default=False, alias="basedpyright.disableLanguageServices")
    auto_import_completions: Annotated[
        bool,
        Checkbox(
            label="Auto Import Completions",
            text="Offer auto-import completions",
            tooltip="Offer auto-import completions for unimported symbols",
        ),
    ] = Field(default=True, alias="basedpyright.analysis.autoImportCompletions")
    inlay_hints_variable_types: Annotated[
        bool,
        Checkbox(
            label="Inlay Hints: Variable Types",
            text="Show variable type hints",
            tooltip="Show inlay hints for variable types in the editor",
        ),
    ] = Field(default=True, alias="basedpyright.analysis.inlayHints.variableTypes")
    inlay_hints_call_argument_names: Annotated[
        bool,
        Checkbox(
            label="Inlay Hints: Call Argument Names",
            text="Show argument names type hints",
            tooltip="Show inlay hints for call argument names in the editor",
        ),
    ] = Field(default=True, alias="basedpyright.analysis.inlayHints.callArgumentNames")
    inlay_hints_function_return_types: Annotated[
        bool,
        Checkbox(
            label="Inlay Hints: Return Types",
            text="Show function return type hints",
            tooltip="Show inlay hints for function return types in the editor",
        ),
    ] = Field(default=True, alias="basedpyright.analysis.inlayHints.functionReturnTypes")
    inlay_hints_generic_types: Annotated[
        bool,
        Checkbox(
            label="Inlay Hints: Generic Types",
            text="Show generic type hints",
            tooltip="Show inlay hints for generic types in the editor",
        ),
    ] = Field(default=True, alias="basedpyright.analysis.inlayHints.genericTypes")
    diagnostic_mode: str = Field(default="workspace", alias="basedpyright.analysis.diagnosticMode")
    extra_paths: list[Path] = Field(default_factory=lambda: [get_stubs_dir()], alias="basedpyright.analysis.extraPaths")


class EditorOptionsSettings(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    theme: Annotated[
        Theme,
        Dropdown(
            label="Editor Theme",
            items=[(t.value, t.value) for t in Theme],
            tooltip="Color theme for the Monaco code editor",
        ),
    ] = Theme.GITHUB_DARK
    font_size: Annotated[
        int,
        Spin(
            label="Font Size",
            min=8,
            max=48,
            tooltip="Control editor font size in pixels",
        ),
    ] = Field(default=14, alias="editor.fontSize")
    font_family: Annotated[
        str,
        LineEdit(
            label="Font Family",
            tooltip="Control editor font family",
        ),
    ] = Field(
        default="'Cascadia Mono', 'Consolas', 'Courier New', monospace",
        alias="editor.fontFamily",
    )
    tab_size: Annotated[
        int,
        Spin(
            label="Tab Size",
            min=1,
            max=8,
            tooltip="Number of spaces per indentation level",
        ),
    ] = Field(default=4, alias="editor.tabSize")
    insert_spaces: Annotated[
        bool,
        Checkbox(
            label="Insert Spaces",
            text="Insert spaces when pressing Tab",
            tooltip="Insert spaces when pressing Tab",
        ),
    ] = Field(default=True, alias="editor.insertSpaces")
    word_wrap: Annotated[
        str,
        Dropdown(
            label="Word Wrap",
            items=[
                ("Off", "off"),
                ("On", "on"),
                ("Word Wrap Column", "wordWrapColumn"),
                ("Bounded", "bounded"),
            ],
            tooltip="Control how lines wrap in the editor",
        ),
    ] = Field(default="off", alias="editor.wordWrap")
    line_numbers: Annotated[
        str,
        Dropdown(
            label="Line Numbers",
            items=[
                ("On", "on"),
                ("Off", "off"),
                ("Relative", "relative"),
                ("Interval", "interval"),
            ],
            tooltip="Control display of line numbers",
        ),
    ] = Field(default="on", alias="editor.lineNumbers")
    minimap_enabled: Annotated[
        bool,
        Checkbox(
            label="Minimap Enabled",
            text="Enable minimap preview",
            tooltip="Show minimap preview on the right side",
        ),
    ] = Field(default=True, alias="editor.minimap.enabled")
    render_whitespace: Annotated[
        str,
        Dropdown(
            label="Render Whitespace",
            items=[
                ("None", "none"),
                ("Boundary", "boundary"),
                ("Selection", "selection"),
                ("Trailing", "trailing"),
                ("All", "all"),
            ],
            tooltip="Render whitespace characters in the editor",
        ),
    ] = Field(default="selection", alias="editor.renderWhitespace")
    cursor_blinking: Annotated[
        str,
        Dropdown(
            label="Cursor Blinking",
            items=[
                ("Blink", "blink"),
                ("Smooth", "smooth"),
                ("Phase", "phase"),
                ("Expand", "expand"),
                ("Solid", "solid"),
            ],
            tooltip="Control cursor blinking animation",
        ),
    ] = Field(default="smooth", alias="editor.cursorBlinking")
    bracket_pair_colorization: Annotated[
        bool,
        Checkbox(
            label="Bracket Pair Colorization",
            text="Enable bracket pair colorization",
            tooltip="Colorize matching bracket pairs",
        ),
    ] = Field(default=True, alias="editor.bracketPairColorization.enabled")


class GlobalSettings(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    options: EditorOptionsSettings = Field(default_factory=EditorOptionsSettings)
    basedpyright: BasedpyrightSettings = Field(default_factory=BasedpyrightSettings)


class LocalSettings(LocalSettingsModel):
    dock_state: str | None = None
