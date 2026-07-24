"""
Detect plugins that have changes since their last versioningit tag.

Usage:

    python .github/scripts/detect_plugin_releases.py

For each plugin with unreleased changes, writes a PR body markdown file to .github/releases/<plugin>.md
and outputs the plugin list to GITHUB_OUTPUT.

Exit codes:

    0 - releases pending
    1 - error
    2 - no releases pending
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLUGINS_DIR = Path("src/plugins")
RELEASES_DIR = Path(".github/releases")
REPO_ROOT = Path(__file__).resolve().parents[2]


def run_git(*args: Any) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(map(str, args))} failed: {result.stderr.strip()}")

    return result.stdout.strip()


def get_latest_tag(plugin_name: str) -> str | None:
    try:
        output = run_git("tag", "-l", f"{plugin_name}/v*", "--sort=-v:refname")
    except RuntimeError:
        return None

    return tags[0] if (tags := output.splitlines()) else None


def count_commits(rel_path: Path, since_tag: str | None = None) -> int:
    return int(run_git("rev-list", "--count", f"{since_tag}..HEAD" if since_tag else "HEAD", "--", rel_path))


def get_commit_log(tag: str | None, rel_path: Path, max_entries: int = 50) -> list[str]:
    args = ["log", "--oneline", "--no-color", "-n", str(max_entries)]

    if tag:
        args.append(f"{tag}..HEAD")

    return run_git(*args, "--", rel_path).splitlines()


@dataclass
class PluginChange:
    plugin: str
    current_tag: str
    commit_count: int
    commits: list[str] = field(default_factory=list)

    def to_release_notes(self) -> str:
        lines = [
            f"## 📦 Release `{self.plugin}`",
            "",
            f"**{self.commit_count}** commit(s) since `{self.current_tag}`:",
            "",
        ]

        lines.extend(f"- {commit}" for commit in self.commits)

        return "\n".join(lines)

    def to_pr_body(self) -> str:
        lines = [
            self.to_release_notes(),
            "",
            "---",
            "",
            "### ⚠️ Action required before merging",
            "",
            "Edit the **PR title** to include the version:",
            "",
            f"    release: {self.plugin}/v<VERSION>",
            "",
            "*Merging this PR will create the tag and trigger PyPI publishing.*",
        ]

        return "\n".join(lines)


def detect_changes() -> list[PluginChange]:
    plugins_path = REPO_ROOT / PLUGINS_DIR

    if not plugins_path.is_dir():
        print(f"Error: plugins directory not found at {plugins_path}", file=sys.stderr)
        sys.exit(1)

    changes = list[PluginChange]()

    for plugin_dir in sorted(plugins_path.iterdir()):
        if not plugin_dir.is_dir():
            continue

        name = plugin_dir.name
        rel_path = PLUGINS_DIR / name
        latest_tag = get_latest_tag(name)
        commit_count = count_commits(rel_path, since_tag=latest_tag)

        if commit_count == 0:
            continue

        changes.append(
            PluginChange(
                plugin=name,
                current_tag=latest_tag or "(none)",
                commit_count=commit_count,
                commits=get_commit_log(latest_tag, rel_path),
            )
        )

    return changes


def main() -> None:
    changes = detect_changes()

    if not changes:
        print("No plugins have unreleased changes.")
        sys.exit(2)

    releases_dir = REPO_ROOT / RELEASES_DIR
    releases_dir.mkdir(parents=True, exist_ok=True)

    for change in changes:
        (releases_dir / f"{change.plugin}.md").write_text(change.to_pr_body(), encoding="utf-8")
        (releases_dir / f"{change.plugin}.release.md").write_text(change.to_release_notes(), encoding="utf-8")

        print(f"  {change.plugin}: {change.commit_count} unreleased commit(s) since {change.current_tag}")

    plugins = json.dumps([c.plugin for c in changes])

    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print(f"\nplugins={plugins}")
        return

    with open(output_file, "a") as f:
        f.write(f"plugins={plugins}\n")


if __name__ == "__main__":
    main()
