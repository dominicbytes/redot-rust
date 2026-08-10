# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate and install a redot-rust-compat archive into a Redot project."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.package_release import MANIFEST_NAME  # noqa: E402


class InstallError(RuntimeError):
    """Raised before an unsafe or inconsistent package can be installed."""


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def inspect_package(package_path: Path, expected_target: str | None = None) -> dict[str, Any]:
    package_path = package_path.resolve()
    if not package_path.is_file():
        raise InstallError(f"package does not exist: {package_path}")
    with zipfile.ZipFile(package_path) as package:
        infos = package.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise InstallError("package contains duplicate archive paths")
        for info in infos:
            if not _safe_name(info.filename):
                raise InstallError(f"unsafe archive path: {info.filename}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise InstallError(f"symbolic links are not allowed: {info.filename}")
        if MANIFEST_NAME not in names:
            raise InstallError(f"package manifest is missing: {MANIFEST_NAME}")
        manifest = json.loads(package.read(MANIFEST_NAME).decode("utf-8"))
        if manifest.get("schema_version") != 1:
            raise InstallError("unsupported package manifest schema")
        if manifest.get("name") != "redot-rust-compat":
            raise InstallError("unexpected package name")
        if expected_target and manifest.get("target") != expected_target:
            raise InstallError(
                f"target mismatch: expected {expected_target}, found {manifest.get('target')}"
            )
        file_rows = manifest.get("files")
        if not isinstance(file_rows, list):
            raise InstallError("manifest files list is missing")
        expected_names = {MANIFEST_NAME}
        for row in file_rows:
            name = row.get("path")
            if not isinstance(name, str) or not _safe_name(name):
                raise InstallError(f"invalid manifest path: {name!r}")
            if name in expected_names:
                raise InstallError(f"duplicate manifest path: {name}")
            expected_names.add(name)
            data = package.read(name)
            actual_hash = hashlib.sha256(data).hexdigest().upper()
            if actual_hash != row.get("sha256"):
                raise InstallError(f"hash mismatch: {name}")
            if len(data) != row.get("size"):
                raise InstallError(f"size mismatch: {name}")
        if set(names) != expected_names:
            extras = sorted(set(names) - expected_names)
            missing = sorted(expected_names - set(names))
            raise InstallError(f"archive inventory mismatch; extras={extras}, missing={missing}")
    return manifest


def install_package(
    package_path: Path,
    project_dir: Path,
    *,
    expected_target: str | None = None,
) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    if not (project_dir / "project.godot").is_file():
        raise InstallError(f"destination is not a Redot project: {project_dir}")
    manifest = inspect_package(package_path, expected_target)
    files = [row["path"] for row in manifest["files"]] + [MANIFEST_NAME]
    collisions = [name for name in files if (project_dir / Path(name)).exists()]
    if collisions:
        raise InstallError(f"installation would overwrite existing files: {collisions}")
    with zipfile.ZipFile(package_path) as package:
        for name in sorted(files):
            destination = project_dir.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(package.read(name))
    return {
        "schema_version": 1,
        "status": "PASS",
        "package": Path(package_path).name,
        "target": manifest["target"],
        "version": manifest["version"],
        "installed_files": sorted(files),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = install_package(args.package, args.project, expected_target=args.target)
    except (InstallError, OSError, KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
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
