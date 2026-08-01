import json
import os
import re
from typing import TypedDict


class PackageMetadata(TypedDict):
    tag: str
    package: str
    path: str
    build_args: str


# Packages that delegate to their own build workflow instead of cd-build-python.yml.
SPECIAL_PACKAGES = {
    "vspackrgb": "vspackrgb",
    "vsview-cli": "vsview-cli",
    "vsview-editor": "vsview-editor",
}


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

    build_workflow = SPECIAL_PACKAGES.get(target, "python")

    # If a package needs special path or name handling, it would go there,
    # Otherwise, we fallback to L48 and assumes we're publishing a plugin
    all_pkgs = [
        PackageMetadata(tag="vsview", package="vsview", path=".", build_args=""),
    ]

    filtered = [p for p in all_pkgs if p["tag"] == target]

    if build_workflow == "python" and not filtered:
        # Not a known special package and not in all_pkgs -> treat as a plugin
        p = PackageMetadata(tag=target, package=f"vsview-{target}", path=f"src/plugins/{target}", build_args="")
        filtered.append(p)

    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print("GITHUB_OUTPUT not set, printing results:")
        print(f"build-workflow={build_workflow}")
        print(f"matrix={json.dumps(filtered)}")
        return

    with open(output_file, "a") as f:
        f.write(f"build-workflow={build_workflow}\n")
        f.write(f"matrix={json.dumps(filtered)}\n")


if __name__ == "__main__":
    main()
