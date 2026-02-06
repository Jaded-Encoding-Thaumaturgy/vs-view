def get_slowpics_headers() -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Access-Control-Allow-Origin": "*",
        "Origin": "https://slow.pics/",
        "Referer": "https://slow.pics/comparison",
        "User-Agent": (
            # f"vs-view (https://github.com/Jaded-Encoding-Thaumaturgy/vs-view {1.0})"  # SlowBro asked for this
            "vs-preview (https://github.com/Jaded-Encoding-Thaumaturgy/vs-preview 0.17.1"  # SlowBro asked for this
        ),
    }
