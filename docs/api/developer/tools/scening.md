---
icon: lucide/clapperboard
---

# Scening Tool API

API for extending the Scening tool with custom parsers and serializers.

---

[:lucide-ruler:{ .middle } **Ranges are inclusive**](#ranges-are-inclusive)

[:lucide-box:{ .middle } **Core Classes**](#core-classes)

[:lucide-database:{ .middle } **Models**](#models)

[:lucide-wrench:{ .middle } **Utility & Hook**](#utility-hook)

---

## Ranges are inclusive

Parsers return and serializers receive **inclusive** ranges:

- For frames, `end` is the last frame. A range from frame 10 to frame 20 holds 11 frames.
- For times, `end` is the start time of the last frame.
- `end=None` is a range of one frame.

The **Enable exclusive (Python-style) ranges** setting does not affect your parser or serializer.
The tool converts only the built-in Python List (Frames) format, and only when the setting is on.
A format that stores exclusive ends (like a Python slice) must add or remove the extra frame itself.

## Core Classes

::: vsview.app.tools.scening.api
    options:
        heading_level: 3
        members:
           - Parser
           - Serializer
           - FileFilter

## Models

::: vsview.app.tools.scening.api
    options:
        heading_level: 3
        members:
           - RangeFrame
           - RangeTime
           - SceneRow
           - UnifiedRange

## Utility & Hook

::: vsview.app.tools.scening.api
    options:
        heading_level: 3
        members:
           - borrowed_text_wrapper
           - hookimpl
