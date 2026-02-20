---
icon: lucide/code
---

# Developer API Overview

This section covers the APIs available for developing plugins and extending VSView.

Most of these symbols are re-exported through the top-level `vsview.api` module for convenience.

---

[:lucide-cpu:{ .middle } **Core & Proxies**](#core-proxies)

[:lucide-layout-template:{ .middle } **Custom Widgets**](#custom-widgets)

[:lucide-settings:{ .middle } **UI Settings**](#ui-settings)

[:lucide-play-circle:{ .middle } **Timeline & Playback**](#timeline-playback)

[:lucide-wrench:{ .middle } **Utilities**](#utilities)

---

## Core & Proxies

::: vsview.api
    options:
        heading_level: 3
        members:
           - PluginAPI
           - VideoOutputProxy
           - AudioOutputProxy
           - GraphicsViewProxy
           - TimelineProxy
           - PlaybackProxy
           - PluginSettings
           - WidgetPluginBase
           - NodeProcessor
           - ActionDefinition
           - LocalSettingsModel


## Custom Widgets

::: vsview.api
    options:
        heading_level: 3
        members:
           - Accordion
           - AnimatedToggle
           - SegmentedControl
           - FrameEdit
           - TimeEdit
           - BaseGraphicsView
           - PluginGraphicsView

## UI Settings

::: vsview.api
    options:
        heading_level: 3
        members:
           - WidgetMetadata 
           - Checkbox
           - Dropdown
           - Spin
           - DoubleSpin
           - PlainTextEdit
           - WidgetTimeEdit


## Timeline & Playback

::: vsview.api
    options:
        heading_level: 3
        members:
           - Frame
           - Time


## Utilities

::: vsview.api
    options:
        heading_level: 3
        members:
           - run_in_background
           - run_in_loop
           - IconName
           - IconReloadMixin
           - hookimpl
