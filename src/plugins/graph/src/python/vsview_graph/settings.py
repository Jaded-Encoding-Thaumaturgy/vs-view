from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel
from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence

from vsview.api import ActionDefinition, Checkbox, Dropdown, Spin

from .constants import PLUGIN_IDENTIFIER


class GraphSettings(BaseModel):
    direction: Annotated[
        Literal["TB", "LR"],
        Dropdown(
            label="Layout Direction",
            items=[("Top to Bottom (Vertical)", "TB"), ("Left to Right (Horizontal)", "LR")],
            tooltip="Default layout orientation for the filter dependency graph.",
        ),
    ] = "TB"

    layout: Annotated[
        Literal["dagre", "breadthfirst", "concentric", "grid"],
        Dropdown(
            label="Layout Algorithm",
            items=[
                ("Dagre (Hierarchical)", "dagre"),
                ("Breadth First", "breadthfirst"),
                ("Concentric", "concentric"),
                ("Grid", "grid"),
            ],
            tooltip=(
                "Graph layout algorithm.\n"
                "Dagre is the only one that honours the layout direction; the others ignore it."
            ),
        ),
    ] = "dagre"

    rank_sep: Annotated[
        int,
        Spin(
            label="Rank Separation",
            min=20,
            max=200,
            suffix=" px",
            tooltip="Distance between dependency ranks in the Dagre layout.",
        ),
    ] = 60

    node_sep: Annotated[
        int,
        Spin(
            label="Node Separation",
            min=10,
            max=200,
            suffix=" px",
            tooltip="Distance between sibling nodes in the Dagre and Concentric layouts.",
        ),
    ] = 40

    heatmap_enabled: Annotated[
        bool,
        Checkbox(
            label="Profiling Heatmap",
            text="Enable profiling heatmap by default",
            tooltip="Highlight nodes using color gradations representing relative execution time.",
        ),
    ] = False

    auto_fit_on_reload: Annotated[
        bool,
        Checkbox(
            label="Auto-Fit Viewport",
            text="Fit graph into viewport on reload",
            tooltip="Automatically zoom and fit the entire graph into the viewport when topology reloads.",
        ),
    ] = True

    show_timings: Annotated[
        bool,
        Checkbox(
            label="Show Timings",
            text="Display execution timings on nodes",
            tooltip="Show execution timings on graph nodes and tooltips.",
        ),
    ] = True

    timing_unit: Annotated[
        Literal["auto", "ms", "ns"],
        Dropdown(
            label="Timing Unit",
            items=[("Automatic", "auto"), ("Milliseconds (ms)", "ms"), ("Nanoseconds (ns)", "ns")],
            tooltip="Display unit for filter processing timings.",
        ),
    ] = "auto"


class GraphShortcut(StrEnum):
    definition: ActionDefinition

    FIT = "fit", "Fit Graph", "F"
    RESET_VIEW = "reset_view", "Reset Zoom", "Ctrl+0"
    TOGGLE_HEATMAP = "toggle_heatmap", "Toggle Heatmap", "H"
    TOGGLE_PATTERNS = "toggle_patterns", "Toggle Edge Patterns", "P"
    TOGGLE_OVERVIEW = "toggle_overview", "Toggle Overview Panel", "L"
    RELOAD = "reload", "Reload Graph", "R"
    FOCUS_SEARCH = "focus_search", "Focus Node Search", "/"
    SELECT_ROOT = "select_root", "Select Root Node", "Home"
    SELECT_DEEPEST_LEAF = "select_deepest_leaf", "Select Deepest Leaf", "End"
    CENTER_SELECTED = "center_selected", "Center Selected Node", "Return"
    NAVIGATE_UP = "navigate_up", "Navigate Up / Cycle Inward", "Up"
    NAVIGATE_DOWN = "navigate_down", "Navigate Down / Cycle Outward", "Down"

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(f"{PLUGIN_IDENTIFIER}.{value}", label, default_key)
        return obj

    @property
    def action_name(self) -> str:
        """CamelCase identifier matching TypeScript KeybindingActions."""
        parts = self.value.split("_")
        return parts[0] + "".join(p.capitalize() for p in parts[1:])

    def to_rule(self, key_sequence: QKeySequence) -> dict[str, Any] | None:
        """Convert a configured QKeySequence into a preconfigured payload for the TS frontend."""
        if key_sequence.isEmpty():
            return None

        combo = cast(QKeyCombination, key_sequence[0])  # type: ignore[index]
        mods = combo.keyboardModifiers()
        key_text = QKeySequence(combo.key()).toString(QKeySequence.SequenceFormat.PortableText)

        dom_key = {
            "Return": "Enter",
            "Up": "ArrowUp",
            "Down": "ArrowDown",
            "Left": "ArrowLeft",
            "Right": "ArrowRight",
        }.get(key_text, key_text)

        return {
            "action": self.action_name,
            "key": dom_key,
            "ctrl": bool(mods & Qt.KeyboardModifier.ControlModifier),
            "shift": bool(mods & Qt.KeyboardModifier.ShiftModifier),
            "alt": bool(mods & Qt.KeyboardModifier.AltModifier),
            "meta": bool(mods & Qt.KeyboardModifier.MetaModifier),
        }
