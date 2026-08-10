# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create a clean Redot project and install one verified target archive."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.install_package import InstallError, install_package  # noqa: E402
from tools.package_release import ADDON_ROOT, TARGETS  # noqa: E402


class CleanInstallError(RuntimeError):
    """Raised when the clean fixture cannot be prepared without ambiguity."""


def prepare_clean_install(
    source_project: Path,
    output_project: Path,
    package: Path,
    target: str,
    profile: str,
) -> dict:
    source_project = source_project.resolve()
    output_project = output_project.resolve()
    if profile not in ("debug", "release"):
        raise CleanInstallError(f"unsupported profile: {profile}")
    if not (source_project / "project.godot").is_file():
        raise CleanInstallError(f"source is not a Redot project: {source_project}")
    if output_project.exists():
        raise CleanInstallError(f"output already exists: {output_project}")
    shutil.copytree(source_project, output_project)
    installation = install_package(package, output_project, expected_target=target)

    project_path = output_project / "project.godot"
    project_text = project_path.read_text(encoding="utf-8")
    project_text, count = re.subn(
        r'^expected_library_profile="[^"]+"$',
        f'expected_library_profile="{profile}"',
        project_text,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise CleanInstallError(f"expected one profile setting, found {count}")
    project_path.write_text(project_text, encoding="utf-8")

    if target not in TARGETS:
        raise CleanInstallError(f"unsupported target: {target}")
    target_config = TARGETS[target]
    selected_library = target_config[f"{profile}_name"]
    selected_path = f"res://{ADDON_ROOT}/bin/{selected_library}"
    extension_path = output_project / ADDON_ROOT / "redot_rust_smoke.gdextension"
    if not extension_path.is_file():
        raise CleanInstallError(f"installed extension manifest is missing: {extension_path}")
    extension_text = extension_path.read_text(encoding="utf-8")
    mapping_keys = "|".join(
        re.escape(target_config[key]) for key in ("debug_key", "release_key")
    )
    extension_text, mapping_count = re.subn(
        rf'^((?:{mapping_keys})\s*=\s*)"[^"]+"$',
        lambda match: f'{match.group(1)}"{selected_path}"',
        extension_text,
        flags=re.MULTILINE,
    )
    if mapping_count != 2:
        raise CleanInstallError(
            f"expected two {target} library mappings, found {mapping_count}"
        )
    extension_path.write_text(extension_text, encoding="utf-8")

    extension_list = output_project / ".godot" / "extension_list.cfg"
    extension_list.parent.mkdir(parents=True, exist_ok=True)
    extension_list.write_text(
        f"res://{ADDON_ROOT}/redot_rust_smoke.gdextension\n", encoding="utf-8"
    )
    (extension_list.parent / "global_script_class_cache.cfg").write_text(
        "list=[]\n", encoding="utf-8"
    )
    report = {
        "schema_version": 1,
        "status": "PASS",
        "target": target,
        "profile": profile,
        "source_file_count": sum(1 for path in source_project.rglob("*") if path.is_file()),
        "installation": installation,
        "extension_list": f"res://{ADDON_ROOT}/redot_rust_smoke.gdextension",
        "profile_mapping_override": {
            "library": selected_library,
            "rewritten_keys": [
                target_config["debug_key"],
                target_config["release_key"],
            ],
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--output-project", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--profile", required=True, choices=("debug", "release"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = prepare_clean_install(
            args.source_project,
            args.output_project,
            args.package,
            args.target,
            args.profile,
        )
    except (CleanInstallError, InstallError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
