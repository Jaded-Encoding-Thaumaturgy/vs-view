import json
import os
import re
import tomllib
from pathlib import Path
from typing import Literal, TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]

BuildWorkflow = Literal["python", "wheels", "editor"]


class PackageMetadata(TypedDict):
    tag: str
    package: str
    path: str
    build_workflow: BuildWorkflow
    build_args: str


def discover_packages() -> dict[str, PackageMetadata]:
    """Discover all packages across root, plugins, workspaces, and auxiliary crates."""
    packages = dict[str, PackageMetadata]()

    search_dirs = [REPO_ROOT, REPO_ROOT / "src" / "vspackrgb"]

    for parent in [REPO_ROOT / "src" / "plugins", REPO_ROOT / "src" / "workspaces"]:
        if parent.is_dir():
            search_dirs.extend([p for p in parent.iterdir() if p.is_dir()])

    for directory in search_dirs:
        pyproject_file = directory / "pyproject.toml"
        if not pyproject_file.is_file():
            continue

        with open(pyproject_file, "rb") as f:
            data = tomllib.load(f)

        project_table = data.get("project", {})
        pkg_name = project_table.get("name")
        if not pkg_name:
            continue

        tool_table = data.get("tool", {})
        vcs_table = tool_table.get("versioningit", {}).get("vcs", {})
        match_patterns = vcs_table.get("match", [])
        if match_patterns and isinstance(match_patterns, list):
            tag_prefix = match_patterns[0].split("/")[0]
        else:
            tag_prefix = directory.name

        # Determine build workflow
        hatch_hooks = tool_table.get("hatch", {}).get("build", {}).get("hooks", {})
        if "cibuildwheel" in tool_table or "hatch-rs" in hatch_hooks:
            build_workflow: BuildWorkflow = "wheels"
        elif "hatch-js" in hatch_hooks or (directory / "package.json").is_file():
            build_workflow = "editor"
        else:
            build_workflow = "python"

        rel_path = "." if directory == REPO_ROOT else directory.relative_to(REPO_ROOT).as_posix()

        meta = PackageMetadata(
            tag=tag_prefix,
            package=pkg_name,
            path=rel_path,
            build_workflow=build_workflow,
            build_args="--sdist --wheel" if directory == REPO_ROOT else "",
        )

        packages[tag_prefix] = meta
        packages[pkg_name] = meta

    return packages


def main() -> None:
    event = os.getenv("GITHUB_EVENT_NAME")
    ref = os.getenv("GITHUB_REF", "")
    dispatch_pkg = os.getenv("INPUT_PACKAGE", "")

    target = ""
    if event == "workflow_dispatch":
        target = dispatch_pkg
    elif ref.startswith("refs/tags/"):
        match = re.match(r"^refs/tags/(.+)/v", ref)
        if match:
            target = match.group(1)

    all_packages = discover_packages()
    selected = all_packages.get(target)

    if not selected:
        raise ValueError(f"Unknown package or tag target: '{target}'")

    build_workflow = selected["build_workflow"]
    matrix = [selected] if build_workflow == "python" else []

    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print(f"build-workflow={build_workflow}")
        print(f"package={selected['package']}")
        print(f"package-dir={selected['path']}")
        print(f"matrix={json.dumps(matrix)}")
        return

    with open(output_file, "a") as f:
        f.write(f"build-workflow={build_workflow}\n")
        f.write(f"package={selected['package']}\n")
        f.write(f"package-dir={selected['path']}\n")
        f.write(f"matrix={json.dumps(matrix)}\n")


if __name__ == "__main__":
    main()
