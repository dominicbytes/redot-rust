# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create a clean fixture copy that forces Redot to load one Cargo profile."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import sha256_file  # noqa: E402


TARGETS = {
    "windows-x86_64": {
        "debug": "redot_rust_smoke.windows.template_debug.x86_64.dll",
        "release": "redot_rust_smoke.windows.template_release.x86_64.dll",
        "keys": ("windows.debug.x86_64", "windows.release.x86_64"),
    },
    "linux-x86_64": {
        "debug": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "release": "libredot_rust_smoke.linux.template_release.x86_64.so",
        "keys": ("linux.debug.x86_64", "linux.release.x86_64"),
    },
    "macos-universal": {
        "debug": "libredot_rust_smoke.macos.template_debug.universal.dylib",
        "release": "libredot_rust_smoke.macos.template_release.universal.dylib",
        "keys": ("macos.debug", "macos.release"),
    },
}


class MaterializeError(RuntimeError):
    """Raised when a profile-specific fixture cannot be created safely."""


def materialize_fixture(
    source_project: Path,
    output_project: Path,
    profile: str,
    target_name: str = "windows-x86_64",
) -> dict[str, object]:
    source_project = source_project.resolve()
    output_project = output_project.resolve()
    if target_name not in TARGETS:
        raise MaterializeError(f"unsupported target: {target_name}")
    if profile not in ("debug", "release"):
        raise MaterializeError(f"unsupported profile: {profile}")
    if not (source_project / "project.godot").is_file():
        raise MaterializeError(f"source is not a Redot project: {source_project}")
    if output_project == source_project or output_project.is_relative_to(source_project):
        raise MaterializeError("output must be outside the source project")
    if output_project.exists():
        raise MaterializeError(f"output already exists: {output_project}")

    target = TARGETS[target_name]
    selected_name = target[profile]
    selected_source = source_project / "bin" / selected_name
    if not selected_source.is_file():
        raise MaterializeError(f"selected profile binary is missing: {selected_source}")

    shutil.copytree(
        source_project,
        output_project,
        ignore=shutil.ignore_patterns(".godot", ".results"),
    )

    extension_path = output_project / "bin" / "redot_rust_smoke.gdextension"
    extension_text = extension_path.read_text(encoding="utf-8")
    selected_res_path = f'res://bin/{selected_name}'
    mapping_keys = "|".join(re.escape(key) for key in target["keys"])
    mapping_pattern = re.compile(
        rf'^((?:{mapping_keys})\s*=\s*)"[^"]+"$', re.MULTILINE
    )
    extension_text, mapping_count = mapping_pattern.subn(
        lambda match: f'{match.group(1)}"{selected_res_path}"', extension_text
    )
    if mapping_count != 2:
        raise MaterializeError(
            f"expected two {target_name} library mappings, found {mapping_count}"
        )
    extension_path.write_text(extension_text, encoding="utf-8")

    project_path = output_project / "project.godot"
    project_text = project_path.read_text(encoding="utf-8")
    project_text, setting_count = re.subn(
        r'^expected_library_profile="[^"]+"$',
        f'expected_library_profile="{profile}"',
        project_text,
        flags=re.MULTILINE,
    )
    if setting_count != 1:
        raise MaterializeError(
            f"expected one library profile setting, found {setting_count}"
        )
    project_path.write_text(project_text, encoding="utf-8")

    result = {
        "schema_version": 1,
        "target": target_name,
        "profile": profile,
        "binary": selected_name,
        "sha256": sha256_file(selected_source),
        "size": selected_source.stat().st_size,
    }
    selection_path = output_project / "bin" / "profile-selection.json"
    selection_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--output-project", required=True, type=Path)
    parser.add_argument("--target", choices=sorted(TARGETS), default="windows-x86_64")
    parser.add_argument("--profile", required=True, choices=("debug", "release"))
    args = parser.parse_args(argv)
    try:
        result = materialize_fixture(
            args.source_project, args.output_project, args.profile, args.target
        )
    except (MaterializeError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
