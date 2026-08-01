import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, override

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.builders.wheel import WheelBuilderConfig
from packaging.version import Version


class CustomBuildHook(BuildHookInterface[WheelBuilderConfig]):
    @override
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        pyversion = Version(self.metadata.version)
        jsversion = pyversion.base_version

        if pyversion.is_devrelease:
            jsversion += f"-dev{pyversion.dev}"

        print("Syncing versions...")
        if not (npm := shutil.which("npm")):
            raise FileNotFoundError("npm not found in PATH. Cannot sync version.")

        subprocess.run(
            [npm, "version", jsversion, "--allow-same-version"],
            check=True,
            shell=os.name == "nt",
            stdout=subprocess.DEVNULL,
        )

        print("Generating the npm SBOMs...", file=sys.stderr)
        if not (npx := shutil.which("npx")):
            raise FileNotFoundError("npx not found in PATH. Cannot generate JS SBOMs.")

        sbomsjs_path = Path(self.root) / "sboms-js.cdx.json"
        cmd: list[Any] = [
            npx,
            "@cyclonedx/cyclonedx-npm",
            "--omit",
            "dev",
            "--sv",
            "1.7",
            "-o",
            sbomsjs_path,
        ]
        subprocess.run(cmd, check=True, shell=os.name == "nt")

        if sbomsjs_path.exists():
            print(f"{sbomsjs_path.name} found...", file=sys.stderr)
            build_data.setdefault("sbom_files", []).append(str(sbomsjs_path.relative_to(self.root)))
        else:
            print(f"{sbomsjs_path.name} not found.", file=sys.stderr)

    @override
    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        for sbom in build_data.get("sbom_files", []):
            Path(sbom).unlink(missing_ok=True)
