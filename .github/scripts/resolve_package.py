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

    # If a package needs special path or name handling, it would go there,
    # Otherwise, we fallback to L39 and assumes we're publishing a plugin
    # fmt: off
    all_pkgs = [
        {"tag": "vsview", "package": "vsview", "path": "."},
    ]
    # fmt: on

    filtered = [p for p in all_pkgs if p["tag"] == target]

    if target == "vspackrgb":
        is_vspackrgb = "is-vspackrgb=true"
    else:
        is_vspackrgb = "is-vspackrgb=false"

        if not filtered:
            filtered.append({"tag": target, "package": f"vsview-{target}", "path": f"src/plugins/{target}"})

    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print("GITHUB_OUTPUT not set, printing results:")
        print(is_vspackrgb)
        print(f"matrix={json.dumps(filtered)}")
        return

    with open(output_file, "a") as f:
        f.write(f"{is_vspackrgb}\n")
        f.write(f"matrix={json.dumps(filtered)}\n")


if __name__ == "__main__":
    main()
