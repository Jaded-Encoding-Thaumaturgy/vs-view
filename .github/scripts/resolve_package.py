import json
import os
import re


def main() -> None:
    event = os.getenv("GITHUB_EVENT_NAME")
    ref = os.getenv("GITHUB_REF", "")
    dispatch_pkg = os.getenv("INPUT_PACKAGE", "")

    # Extract target package tag
    target = ""

    if event == "workflow_dispatch":
        target = dispatch_pkg
    elif ref.startswith("refs/tags/"):
        # Extract 'package' from 'refs/tags/package/v1.0.0'
        match = re.match(r"^refs/tags/(.+)/v", ref)

        if match:
            target = match.group(1)

    # fmt: off
    all_pkgs = [
        {"tag": "vsview", "package": "vsview", "path": "."},
        {"tag": "audio-convert", "package": "vsview-audio-convert", "path": "src/plugins/audio-convert"},
        {"tag": "fftspectrum", "package": "vsview-fftspectrum", "path": "src/plugins/fftspectrum"},
        {"tag": "frameprops-extended", "package": "vsview-frameprops-extended", "path": "src/plugins/frameprops-extended"},  # noqa: E501
        {"tag": "split-planes", "package": "vsview-split-planes", "path": "src/plugins/split-planes"},
    ]
    # fmt: on

    filtered = [p for p in all_pkgs if p["tag"] == target]

    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print("GITHUB_OUTPUT not set, printing results:")
        print(f"is-vspackrgb={'true' if target == 'vspackrgb' else 'false'}")
        print(f"matrix={json.dumps(filtered)}")
        return

    with open(output_file, "a") as f:
        f.write(f"is-vspackrgb={'true' if target == 'vspackrgb' else 'false'}\n")
        f.write(f"matrix={json.dumps(filtered)}\n")


if __name__ == "__main__":
    main()
