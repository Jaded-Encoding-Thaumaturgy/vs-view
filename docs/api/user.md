---
icon: lucide/user
---

# User API

The user-facing API is used primarily within VapourSynth scripts to register nodes for preview.

::: vsview.api
    options:
        heading_level: 3
        members:
           - set_output
           - catch_output
           - is_preview
           - is_reload
           - get_reload_count
           - get_state
           - get_cached
           - on_workspace_destroy
