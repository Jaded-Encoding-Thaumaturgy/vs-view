from __future__ import annotations

import urllib.request
import zlib
from pathlib import Path


def fix_inventory(input_path: Path, output_path: Path) -> None:
    """
    Patches VapourSynth objects.inv to add module prefixes to names while preserving anchors.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.exists():
        print(f"{input_file} not found locally. Downloading from VapourSynth...")
        try:
            urllib.request.urlretrieve("https://www.vapoursynth.com/doc/objects.inv", input_file)
        except Exception as e:
            print(f"Error downloading inventory: {e}")
            return

    data = input_file.read_bytes()

    marker = b"compressed using zlib.\n"
    if marker not in data:
        marker = b"compressed with zlib.\n"

    try:
        header_part, compressed_part = data.split(marker, 1)
    except ValueError:
        print("Error: Could not find compression marker in inventory.")
        return

    header = header_part.decode("ascii", errors="ignore")
    header = header.replace("Project: VapourSynth", "Project: vapoursynth")

    decompressed = zlib.decompress(compressed_part)
    content = decompressed.decode("utf-8")
    lines = content.splitlines()

    fixed_lines = []
    # VapourSynth classes from the inventory
    targets = (
        # Classes
        "AudioFormat",
        "AudioFrame",
        "AudioNode",
        "Core",
        "Environment",
        "EnvironmentData",
        "EnvironmentPolicy",
        "EnvironmentPolicyAPI",
        "Error",
        "Func",
        "Function",
        "Local",
        "Plugin",
        "VideoFormat",
        "VideoFrame",
        "VideoNode",
        "VideoOutputTuple",
        "RawNode",
        "RawFrame",
        # Core singleton
        "core",
        # Functions
        "construct_signature",
        "clear_output",
        "clear_outputs",
        "get_outputs",
        "get_output",
        "register_on_destroy",
        "unregister_on_destroy",
        "get_current_environment",
        "register_policy",
        "_try_enable_introspection",
        "has_policy",
    )
    for line in lines:
        if not line or line.startswith("#"):
            fixed_lines.append(line)
            continue

        parts = line.split()
        if len(parts) < 4:
            fixed_lines.append(line)
            continue

        name = parts[0]

        # Match if name is in targets or starts with target.member
        is_target = any(name == t or name.startswith(t + ".") for t in targets)

        if is_target:
            # Fix the anchor in the location (parts[3])
            location = parts[3]
            # If it uses $, replace with the original name
            if "$" in location:
                location = location.replace("$", name)

            # Ensure the anchor doesn't have the vapoursynth. prefix
            if "#" in location:
                base, anchor = location.split("#", 1)
                # Strip vapoursynth. prefix from anchor if present
                anchor = anchor.removeprefix("vapoursynth.")
                parts[3] = f"{base}#{anchor}"
            else:
                parts[3] = location

            # Prefix the name with vapoursynth.
            parts[0] = f"vapoursynth.{name}"
            fixed_lines.append(" ".join(parts))
        else:
            fixed_lines.append(line)

    fixed_content = "\n".join(fixed_lines) + "\n"
    fixed_part = zlib.compress(fixed_content.encode("utf-8"))

    output_file.write_bytes(header.encode("ascii") + marker + fixed_part)
    print(f"Successfully created patched inventory: {output_file}")


if __name__ == "__main__":
    # Assumes we run project root
    root = Path(__file__).parent.parent
    fix_inventory(root / "docs" / "vs_objects.inv", root / "docs" / "vs_fixed.inv")
