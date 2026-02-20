---
icon: lucide/webhook
---

# Hook Specifications

VSView uses `pluggy` for its plugin system. Register your implementations using the markers defined here.

---

[:lucide-layout-panel-left:{ .middle } **UI Hooks**](#ui-hooks)

[:lucide-layers:{ .middle } **Processing Hooks**](#processing-hooks)

[:lucide-webhook:{ .middle } **Hook**](#hook)

---

## UI Hooks

::: vsview.app.plugins.specs
    options:
        heading_level: 3
        members:
           - vsview_register_tooldock
           - vsview_register_toolpanel

## Processing Hooks

::: vsview.app.plugins.specs
    options:
        heading_level: 3
        members:
           - vsview_get_video_processor
           - vsview_get_audio_processor


## Hook

::: vsview.app.plugins.specs
    options:
        heading_level: 3
        members:
           - hookimpl
