import os
import shutil
import subprocess
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
            jsversion += "-dev"

        self.app.display_debug("Syncing versions...")
        if not (npm := shutil.which("npm")):
            raise FileNotFoundError("npm not found in PATH. Cannot sync version.")

        subprocess.run(
            [npm, "version", jsversion, "--allow-same-version"],
            check=True,
            shell=os.name == "nt",
            stdout=subprocess.DEVNULL,
        )

        if self.target_name != "wheel":
            self.app.display_debug("Skipping SBOM generation: target_name != wheel")
            return

        if version == "editable" or len(build_data.setdefault("force_include_editable", [])) > 0:
            self.app.display_debug("Skipping SBOM generation: editable build")
            return

        self.app.display_debug("Generating the npm SBOMs..")

        sbomsjs_path = Path(self.root) / "sboms-js.cdx.json"
        with sbomsjs_path.open("w") as f:
            subprocess.run(["npm", "run", "--silent", "sboms"], check=True, shell=os.name == "nt", stdout=f)

        if sbomsjs_path.exists():
            self.app.display_debug(f"{sbomsjs_path.name} found...")
            build_data.setdefault("sbom_files", []).append(str(sbomsjs_path.relative_to(self.root)))
        else:
            self.app.display_warning(f"{sbomsjs_path.name} not found.")

    @override
    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        for sbom in build_data.get("sbom_files", []):
            Path(sbom).unlink(missing_ok=True)
