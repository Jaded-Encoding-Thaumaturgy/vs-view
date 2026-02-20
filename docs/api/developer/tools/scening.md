---
icon: lucide/clapperboard
---

# Scening Tool API

API for extending the Scening tool with custom parsers and serializers.

---

[:lucide-box:{ .middle } **Core Classes**](#core-classes)

[:lucide-database:{ .middle } **Models**](#models)

[:lucide-wrench:{ .middle } **Utility & Hook**](#utility-hook)

---

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
