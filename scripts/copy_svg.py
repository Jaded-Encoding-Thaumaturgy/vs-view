import shutil
from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Option, Typer, echo

app = Typer()

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "src" / "vsview" / "assets" / "icons"


@app.command()
def copy(
    name: Annotated[str, Argument(help="Name of the icon to copy")],
    provider: Annotated[str, Option(help="Provider: phosphor, material or lucide")] = "phosphor",
) -> None:
    """Copy icon assets from submodule to src/vsview/assets/{provider}."""

    if provider == "phosphor":
        src = ROOT / "submodules" / "phosphor" / "assets"
        suffixes = ("", "-bold", "-duotone", "-fill", "-light", "-thin")
    elif provider == "material":
        src = ROOT / "submodules" / "material" / "svg"
        suffixes = ("", "-outline")
    elif provider == "lucide":
        src = ROOT / "submodules" / "lucide" / "icons"
        suffixes = ("",)
    else:
        echo(f"Unknown provider: {provider}", err=True)
        raise Exit(1)

    if not src.exists():
        echo(f"Source directory not found: {src}", err=True)
        raise Exit(1)

    dst = DST / provider

    dst.mkdir(parents=True, exist_ok=True)

    count = 0
    for suffix in suffixes:
        for file in src.rglob(f"{name}{suffix}.svg"):
            shutil.copy2(file, dst / file.name)
            count += 1

    if count == 0:
        echo(f"No files found matching '{name}' in {src}", err=True)
        raise Exit(1)

    echo(f"Copied {count} files matching '{name}': {src} -> {dst}")


if __name__ == "__main__":
    app()
