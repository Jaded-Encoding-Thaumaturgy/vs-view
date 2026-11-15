"""
Create and push an annotated git tag.

Usage:

    python .github/scripts/push_release_tags.py <tag> [--notes-file <path>]
"""

import argparse
import subprocess
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version
from resolve_package import discover_packages

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_git(*args: Any) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(map(str, args))} failed: {result.stderr.strip()}")

    return result.stdout.strip()


def parse_release_tag(tag: str, known: Collection[str]) -> tuple[str, Version]:
    tag = tag.strip()

    if "/v" not in tag:
        raise ValueError(f"tag {tag!r} must be in the format <plugin>/v<version>")

    plugin, version = tag.split("/v", 1)

    if not plugin:
        raise ValueError("plugin name is empty")

    if plugin not in known:
        raise ValueError(f"unknown package {plugin!r}. Known packages: {sorted(known)}")

    try:
        return plugin, Version(version)
    except InvalidVersion:
        raise ValueError(f"version {version!r} is not a valid PEP 440 version") from None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and push an annotated release tag.")
    parser.add_argument("tag", help="Tag in the format <plugin>/v<version>")
    parser.add_argument(
        "--notes-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to a file whose contents become the tag annotation message.",
    )
    args = parser.parse_args()

    tag: str = args.tag.strip()

    try:
        parse_release_tag(tag, {meta["tag"] for meta in discover_packages().values()})
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Check if tag already exists
    if run_git("tag", "-l", tag):
        print(f"Error: tag {tag} already exists", file=sys.stderr)
        sys.exit(1)

    print(f"🏷️  Creating tag: {tag}")

    if args.notes_file is not None:
        if not args.notes_file.exists():
            print(f"Error: notes file not found: {args.notes_file}", file=sys.stderr)
            sys.exit(1)
        run_git("tag", "-a", "-F", str(args.notes_file), tag)
    else:
        run_git("tag", "-a", "-m", tag, tag)

    print(f"📤 Pushing tag {tag}...")
    run_git("push", "origin", tag)

    print(f"✅ Tag {tag} pushed. The publish workflow should trigger automatically.")


if __name__ == "__main__":
    main()
