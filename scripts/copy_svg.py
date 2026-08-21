# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "cyclopts>=5.0.0b1",
# ]
# ///
import shutil
from pathlib import Path
from typing import Literal

import cyclopts

app = cyclopts.App()

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "src" / "vsview" / "assets" / "icons"


@app.command
def copy(name: str, provider: Literal["phosphor", "material", "lucide"]) -> None:
    """Copy icon assets from submodule to src/vsview/assets/{provider}.

    Args:
        name: Name of the icon to copy.
        provider: Provider.
    """

    if provider == "phosphor":
        src = ROOT / "submodules" / "phosphor" / "assets"
        suffixes = ("", "-bold", "-duotone", "-fill", "-light", "-thin")
    elif provider == "material":
        src = ROOT / "submodules" / "material" / "svg"
        suffixes = ("", "-outline")
    elif provider == "lucide":
        src = ROOT / "submodules" / "lucide" / "icons"
        suffixes = ("",)

    if not src.exists():
        raise FileNotFoundError(f"Source directory not found: {src}")

    dst = DST / provider

    dst.mkdir(parents=True, exist_ok=True)

    count = 0
    for suffix in suffixes:
        for file in src.rglob(f"{name}{suffix}.svg"):
            shutil.copy2(file, dst / file.name)
            count += 1

    if count == 0:
        raise ValueError(f"No files found matching '{name}' in {src}")

    app.console.print(f"Copied {count} files matching '{name}': {src} -> {dst}")


if __name__ == "__main__":
    app()
