from enum import StrEnum
from typing import Self, override


class ActionDefinition(str):
    """
    Unified definition and identifier for a shortcut action.
    """

    label: str
    """Human-readable display label"""

    default_key: str
    """Default key sequence (can be empty)"""

    def __new__(cls, id: str, label: str, default_key: str = "") -> Self:
        self = super().__new__(cls, id)
        self.label = label
        self.default_key = default_key
        return self

    @override
    def __repr__(self) -> str:
        return f"ActionDefinition({super().__repr__()}, label={self.label!r}, default_key={self.default_key!r})"


class ActionID(StrEnum):
    """Identifiers for keyboard shortcut actions."""

    definition: ActionDefinition

    # Menu actions
    LOAD_SCRIPT = "menu.new.load_script", "Load Script", "Ctrl+O"
    LOAD_FILE = "menu.new.load_file", "Load File", "Ctrl+Shift+O"
    WORKSPACE_SCRIPT = "menu.new.workspace.new_script", "New Script Workspace", ""
    WORKSPACE_FILE = "menu.new.workspace.new_file", "New File Workspace", ""
    WORKSPACE_QUICK_SCRIPT = "menu.new.workspace.new_quick_script", "New Quick Script", ""

    # Workspace
    RELOAD = "workspace.loader.reload", "Reload Script", "Ctrl+R"
    RUN_QUICK_SCRIPT = "workspace.quickscript.run", "Run Quick Script", "F5"

    # Tab manager
    SYNC_PLAYHEAD = "workspace.loader.tab.sync_playhead", "Sync Playhead", ""
    SYNC_ZOOM = "workspace.loader.tab.sync_zoom", "Sync Zoom", ""
    SYNC_SCROLL = "workspace.loader.tab.sync_scroll", "Sync Scroll", ""
    AUTOFIT_ALL_VIEWS = "workspace.loader.tab.autofit_all_views", "Global Autofit", "Ctrl+A"

    # View actions
    RESET_ZOOM = "workspace.loader.view.reset_zoom", "Reset Zoom", "Esc"
    AUTOFIT = "workspace.loader.view.autofit", "Autofit View", "Ctrl+Shift+A"
    TOGGLE_SAR = "workspace.loader.view.sar", "Toggle SAR view", ""
    SAVE_CURRENT_IMAGE = "workspace.loader.view.save_current_image", "Save Current Image", "Ctrl+Shift+S"
    COPY_IMAGE_TO_CLIPBOARD = "workspace.loader.view.copy_image_to_clipboard", "Copy Image to Clipboard", "Ctrl+S"
    TOGGLE_PLUGIN_DOCK = "workspace.loader.view.toggle_plugin_dock", "Toggle Plugin Dock", ""
    TOGGLE_PLUGIN_PANEL = "workspace.loader.view.toggle_plugin_panel", "Toggle Plugin Panel", "Ctrl+P"

    # Timeline actions
    COPY_CURRENT_FRAME = "workspace.loader.timeline.copy_current_frame", "Copy Current Frame", "S"
    COPY_CURRENT_TIME = "workspace.loader.timeline.copy_current_time", "Copy Current Time", "Shift+S"
    PLAY_PAUSE = "workspace.loader.timeline.play_pause", "Play / Pause", "Space"
    SEEK_PREVIOUS_FRAME = "workspace.loader.timeline.seek_previous_frame", "Seek Previous Frame", "Left"
    SEEK_NEXT_FRAME = "workspace.loader.timeline.seek_next_frame", "Seek Next Frame", "Right"
    SEEK_N_FRAMES_BACK = "workspace.loader.timeline.seek_n_frames_back", "Seek N Frames Back", "Shift+Left"
    SEEK_N_FRAMES_FORWARD = "workspace.loader.timeline.seek_n_frames_forward", "Seek N Frames Forward", "Shift+Right"
    SEEK_FIRST_FRAME = "workspace.loader.timeline.seek_first_frame", "Seek First Frame", ""
    SEEK_LAST_FRAME = "workspace.loader.timeline.seek_last_frame", "Seek Last Frame", ""
    JUMP_TO_TIME = "workspace.loader.timeline.jump_to_time", "Jump to Time", "Ctrl+F"
    JUMP_TO_FRAME = "workspace.loader.timeline.jump_to_frame", "Jump to Frame", "Ctrl+G"

    # Tab switching
    SWITCH_TAB_0 = "workspace.loader.tab.switch_0", "Switch to Output 0", "1"
    SWITCH_TAB_1 = "workspace.loader.tab.switch_1", "Switch to Output 1", "2"
    SWITCH_TAB_2 = "workspace.loader.tab.switch_2", "Switch to Output 2", "3"
    SWITCH_TAB_3 = "workspace.loader.tab.switch_3", "Switch to Output 3", "4"
    SWITCH_TAB_4 = "workspace.loader.tab.switch_4", "Switch to Output 4", "5"
    SWITCH_TAB_5 = "workspace.loader.tab.switch_5", "Switch to Output 5", "6"
    SWITCH_TAB_6 = "workspace.loader.tab.switch_6", "Switch to Output 6", "7"
    SWITCH_TAB_7 = "workspace.loader.tab.switch_7", "Switch to Output 7", "8"
    SWITCH_TAB_8 = "workspace.loader.tab.switch_8", "Switch to Output 8", "9"
    SWITCH_TAB_9 = "workspace.loader.tab.switch_9", "Switch to Output 9", "0"
    SWITCH_TAB_10 = "workspace.loader.tab.switch_10", "Switch to Output 10", ""
    SWITCH_TAB_11 = "workspace.loader.tab.switch_11", "Switch to Output 11", ""
    SWITCH_TAB_12 = "workspace.loader.tab.switch_12", "Switch to Output 12", ""
    SWITCH_TAB_13 = "workspace.loader.tab.switch_13", "Switch to Output 13", ""
    SWITCH_TAB_14 = "workspace.loader.tab.switch_14", "Switch to Output 14", ""
    SWITCH_TAB_15 = "workspace.loader.tab.switch_15", "Switch to Output 15", ""
    SWITCH_TAB_16 = "workspace.loader.tab.switch_16", "Switch to Output 16", ""
    SWITCH_TAB_17 = "workspace.loader.tab.switch_17", "Switch to Output 17", ""
    SWITCH_TAB_18 = "workspace.loader.tab.switch_18", "Switch to Output 18", ""
    SWITCH_TAB_19 = "workspace.loader.tab.switch_19", "Switch to Output 19", ""
    SWITCH_PREVIOUS_TAB = "workspace.loader.tab.switch_previous_tab", "Switch to Previous Output", ""
    SWITCH_NEXT_TAB = "workspace.loader.tab.switch_next_tab", "Switch to Next Output", ""

    def __new__(cls, value: str, label: str, default_key: str = "") -> Self:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.definition = ActionDefinition(value, label, default_key)
        return obj
