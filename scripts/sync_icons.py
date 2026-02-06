"""Sync all existing icons from their submodule sources."""

import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ICONS_DIR = ROOT / "src" / "vsview" / "assets" / "icons"

PROVIDERS: dict[str, dict[str, Any]] = {
    "phosphor": {
        "src": ROOT / "submodules" / "phosphor" / "assets",
        "suffixes": ("", "-bold", "-duotone", "-fill", "-light", "-thin"),
    },
    "material": {
        "src": ROOT / "submodules" / "material" / "svg",
        "suffixes": ("", "-outline"),
    },
    "lucide": {
        "src": ROOT / "submodules" / "lucide" / "icons",
        "suffixes": ("",),
    },
}


def get_base_names(provider: str) -> set[str]:
    if not (provider_dir := ICONS_DIR / provider).exists() or not (config := PROVIDERS.get(provider)):
        return set()

    base_names = set[str]()

    for svg_file in provider_dir.glob("*.svg"):
        name = svg_file.stem

        for suffix in config["suffixes"]:
            if suffix and name.endswith(suffix):
                base_names.add(name[: -len(suffix)])
                break
        else:
            base_names.add(name)

    return base_names


def sync_provider(provider: str) -> int:
    if not (config := PROVIDERS.get(provider)):
        print(f"Unknown provider: {provider}")
        return 0

    src_dir: Path = config["src"]

    if not src_dir.exists():
        print(f"Source directory not found: {src_dir}")
        return 0

    dst_dir = ICONS_DIR / provider
    base_names = get_base_names(provider)

    if not base_names:
        print(f"No existing icons found for {provider}")
        return 0

    count = 0

    for base_name in base_names:
        for suffix in config["suffixes"]:
            for file in src_dir.rglob(f"{base_name}{suffix}.svg"):
                shutil.copy2(file, dst_dir / file.name)
                count += 1

    print(f"Synced {count} files for {provider}")
    return count


def main() -> None:
    total = 0

    for provider in PROVIDERS:
        total += sync_provider(provider)

    print(f"\nTotal: {total} files synced")


if __name__ == "__main__":
    main()
