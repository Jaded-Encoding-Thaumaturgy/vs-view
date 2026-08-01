---
icon: lucide/file-stack
---

# Workspaces

**VSView** currently provides three built-in workspace types, and one plugin workspace type:

- [**Script Workspace**](script.md): The primary environment for VapourSynth script development and previewing.
- [**File Workspace**](file.md): For directly previewing media files without a script.
- [**Quick Script**](quick-script.md): A workspace for one-off testing and experiments using an integrated code editor.
- [**Editor Workspace**](editor.md): A workspace for VapourSynth script development using an integrated code editor and Python environment.

These workspaces share the same core interface. The sections below describe behavior that applies to all three unless stated otherwise.

## Overview

When you load a script or file into a workspace, the interface is split into the following areas:

<figure markdown="span">
    ![](../../assets/workspace_overview.png){ .lightboxOn }
</figure>

1. [**Workspace Tabs**](#1-workspace-tabs): Output tabs for the currently loaded script or file.
2. [**Frame Viewer**](#2-frame-viewer): Displays the current frame of the active output.
3. [**Playback Controls**](#3-playback-controls): Video and audio transport controls.
4. [**Timeline**](#4-timeline): Frame/time navigation for the active output.
5. [**Tool Docks**](#5-tool-docks): Plugin tools as dockable windows.
6. [**Tool Panels**](#6-tool-panels): Plugin tools as a tabbed side panel.
7. [**Status Bar**](#7-status-bar): Workspace state, media details, and runtime messages.

## 1. Workspace Tabs

Workspace tabs represent each available output from the currently loaded source.

- In **Script Workspace** and **Quick Script**, multiple outputs can be exposed and switched with tabs.
- In **File Workspace**, there is only one output tab.
- Selecting a tab updates the Frame Viewer, Timeline, and related tools to that output.

Additionally, the top-right corner of the tab bar contains buttons that control cross-tab behavior and panel visibility:
<figure markdown="span">
    ![](../../assets/workspace_tab_buttons.png){ loading=lazy }
</figure>
- **Toggle Plugin Tool Docks**: Shows or hides the side Tool Docks area used by plugin tools.
- **Sync playhead**: Cycles through four modes for cross-tab timeline sync:
    - **Adaptive link**: Follows the current Timeline display mode (Time or Frame).
    - **Link by time**: Seeks all tabs to matching timestamps.
    - **Link by frame**: Seeks all tabs to matching frame numbers.
    - **Unlink**: Disables cross-tab playhead sync.
    The tooltip updates live to show the current active mode.
- **Sync zoom**: Links zoom level across outputs. Zooming in one tab applies the same zoom to all tabs.
- **Sync scroll**: Links pan/scroll position across outputs. Panning in one tab updates the viewed region in all tabs.
- **Autofit all views**: Automatically fits each output to the Frame Viewer viewport when enabled.
- **Toggle Plugin Tool Panel**: Shows or hides the side Tool Panel area used by plugin tools.


## 2. Frame Viewer

The Frame Viewer is the main preview area.
It displays the current frame from the active output tab.
Any playback action, play, scrub, or step forward/backward, refreshes this view in real time.

Right-clicking the displayed frame opens the following context menu:

<figure markdown="span">
    ![](../../assets/frame_viewer_context_menu.png){ loading=lazy }
</figure>

- **Zoom**: Zoom in and out. Also available via **++ctrl++** + **mouse wheel**.
- **Autofit**: Fits the current output to the Frame Viewer viewport.
- **Toggle SAR**: Toggles display of sample aspect ratio (SAR) correction for the current output (available only when SAR is not 1:1).
- **Save Current Image**: Opens a save dialog and writes the currently displayed frame to disk.
- **Copy Image to Clipboard**: Copies the currently displayed frame to the system clipboard.

## 3. Playback Controls

Playback Controls combine transport buttons, direct navigation fields, and audio controls.

<figure markdown="span">
    ![](../../assets/playback_controls.png){ loading=lazy }
</figure>

- **Seek N Backward / Forward**: Jumps by the configured seek step.
- **Seek 1 Backward / Forward**: Moves exactly one frame.
- **Play / Pause**: Starts or stops playback. During playback, seek buttons and time/frame fields are disabled, but Play / Pause remains available to stop playback.
- **Time field**: Seeks directly to a specific timestamp.
- **Frame field**: Seeks directly to a specific frame number.
- **Mute + Volume**: Toggles mute and adjusts playback volume (enabled only when audio outputs are available).

Right-click the Playback Controls area for advanced settings:

<figure markdown="span">
    ![](../../assets/playback_controls_context_menu.png){ loading=lazy }
</figure>

- **Seek Step**: Sets the N-frame jump size, with **Reset to Global** to restore the default value from the global settings.
- **Speed Limit**: Sets playback speed from 0.25x to 8.00x in 0.25x steps, with a reset button for 1.00x.
- **Uncap FPS**: Removes the speed limit.
- **Zone Playback**: Defines a zone by time/frame, then plays that zone with optional **Loop** and configurable **Play Step** (negative values allowed!).
- **Audio Output / Delay**: Selects audio output and adjusts sync delay (-10000 ms to +10000 ms), with **Reset to Global** to restore the default value from the global settings.

## 4. Timeline

The Timeline provides precise navigation and visual reference markers for the active output.

- **Scrub with left-click + drag** on the timeline bar to seek interactively.
- **Hover preview**: Moving the mouse over the timeline shows the current hover position as frame/time; optional hover zoom can be enabled in timeline settings.
- **Frame / Time display modes**: The scale and labels can be shown as frame numbers or timestamps.
- **Custom markers/notches**: Plugins and tools can add timeline markers (for example, bookmarks/keyframe-like markers), including optional ranges.

Right-click the Timeline to switch display mode:

- **Frame**: Shows frame-based scale labels.
- **Time**: Shows timestamp-based scale labels.


## 5. Tool Docks

Tool docks are movable plugin windows attached to the workspace layout.
You can drag a dock by its title bar to reposition it, dock it to an edge, stack it with other docks, or leave it floating as a separate window.

You can also toggle their visibility by using the **Toggle Plugin Tool Docks** button in the [Workspace Tabs](#1-workspace-tabs).

<figure markdown="span">
    <video controls width="100%" style="border-radius: 8px;">
        <source src="../../assets/tool_dock_showoff.mp4" type="video/mp4">
        Your browser does not support the video tag.
    </video>
</figure>

See [Plugins](../../plugins/overview.md) for more information.

## 6. Tool Panels

Tool Panels provide the same plugin tooling in a compact tabbed panel located on the right side of the workspace. 

The panel is part of a **horizontal splitter**, which means you can click and drag the handle between the main content and the panel to resize it.

You can also hide the panel by dragging it to the edge or by using the **Toggle Plugin Tool Panel** button in the [Workspace Tabs](#1-workspace-tabs).

<figure markdown="span">
    <video controls width="100%" style="border-radius: 8px;">
        <source src="../../assets/tool_panel_showoff.mp4" type="video/mp4">
        Your browser does not support the video tag.
    </video>
</figure>

See [Plugins](../../plugins/overview.md) for more information.

## 7. Status Bar

The Status Bar shows live information about the active workspace / output

- Runtime/log messages for the workspace and its associated plugins.
- Contextual media information for the selected output.
