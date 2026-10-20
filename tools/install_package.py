# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate and install a redot-rust-compat archive into a Redot project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.package_release import ADDON_ROOT, MANIFEST_NAME
from tools.redot_api import IdentityError, validate_lock  # noqa: E402


class InstallError(RuntimeError):
    """Raised before an unsafe or inconsistent package can be installed."""


MAX_MEMBERS = 4096
MAX_MANIFEST_SIZE = 1024 * 1024
MAX_FILE_SIZE = 256 * 1024 * 1024
MAX_TOTAL_SIZE = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
CHUNK_SIZE = 1024 * 1024
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"} | {
    prefix + suffix for prefix in ("COM", "LPT") for suffix in "123456789\u00b9\u00b2\u00b3"
}


def _safe_name(name: str) -> bool:
    parts = name.split("/")
    return (
        len(parts) > len(ADDON_ROOT.parts)
        and parts[: len(ADDON_ROOT.parts)] == list(ADDON_ROOT.parts)
        and not any(character in name for character in '\\:<>"|?*')
        and not any(ord(character) < 32 for character in name)
        and all(
            part not in ("", ".", "..")
            and not part.endswith((".", " "))
            and part.split(".")[0].rstrip(" ").upper() not in RESERVED_NAMES
            for part in parts
        )
    )


def _destination_key(name: str) -> str:
    if sys.platform == "darwin":
        return unicodedata.normalize("NFD", name).casefold()
    return name.casefold() if sys.platform == "win32" else name


def _inventory(package: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = package.infolist()
    if len(infos) > MAX_MEMBERS:
        raise InstallError("package exceeds member count limit")
    inventory: dict[str, zipfile.ZipInfo] = {}
    keys: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        if info.orig_filename != name or not _safe_name(name):
            raise InstallError(f"unsafe archive path: {name!r}")
        key = _destination_key(name)
        if key in keys:
            raise InstallError("package contains duplicate archive destinations")
        keys.add(key)
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type not in (0, stat.S_IFREG):
            raise InstallError(f"only regular files are allowed: {name}")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise InstallError("unsupported archive compression method")
        limit = MAX_MANIFEST_SIZE if name == MANIFEST_NAME else MAX_FILE_SIZE
        if info.file_size > limit:
            raise InstallError(f"archive member exceeds size limit: {name}")
        total += info.file_size
        if total > MAX_TOTAL_SIZE:
            raise InstallError("package exceeds total uncompressed size limit")
        if info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
            raise InstallError(f"archive member exceeds compression ratio limit: {name}")
        inventory[name] = info
    for key in keys:
        if any(str(parent) in keys for parent in PurePosixPath(key).parents):
            raise InstallError("archive file conflicts with a destination directory")
    return inventory


def _stream_member(package: zipfile.ZipFile, info: zipfile.ZipInfo, output: Any = None) -> str:
    digest = hashlib.sha256()
    count = 0
    with package.open(info) as source:
        while chunk := source.read(CHUNK_SIZE):
            count += len(chunk)
            if count > info.file_size:
                raise InstallError(f"expanded member exceeds declared size: {info.filename}")
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    if count != info.file_size:
        raise InstallError(f"expanded member size mismatch: {info.filename}")
    return digest.hexdigest().upper()


def _inspect_package(package: zipfile.ZipFile, expected_target: str | None) -> dict[str, Any]:
    inventory = _inventory(package)
    if MANIFEST_NAME not in inventory:
        raise InstallError(f"package manifest is missing: {MANIFEST_NAME}")
    with package.open(inventory[MANIFEST_NAME]) as source:
        manifest = json.loads(source.read(MAX_MANIFEST_SIZE + 1).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise InstallError("unsupported package manifest schema")
    if "redot" in manifest or "gdext" in manifest:
        raise InstallError("mixed v1/v2 package identity")
    try:
        binding = manifest.get("binding_abi", {})
        validate_lock({"schema_version": 2, "binding_abi": binding,
                       "certified_engines": manifest.get("certified_engines"),
                       "default_engine_profile": binding.get("engine_profile")})
        if manifest.get("coverage") != binding["coverage"]:
            raise IdentityError("manifest coverage differs from binding ABI")
    except (IdentityError, AttributeError) as error:
        raise InstallError("invalid package identity contract") from error
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
        name = row.get("path") if isinstance(row, dict) else None
        if not isinstance(name, str) or not _safe_name(name):
            raise InstallError(f"invalid manifest path: {name!r}")
        if name in expected_names:
            raise InstallError(f"duplicate manifest path: {name}")
        expected_names.add(name)
        if name not in inventory:
            raise InstallError(f"manifest member is missing: {name}")
        if type(row.get("size")) is not int or inventory[name].file_size != row["size"]:
            raise InstallError(f"size mismatch: {name}")
    if set(inventory) != expected_names:
        raise InstallError("archive inventory does not match manifest")
    for row in file_rows:
        if _stream_member(package, inventory[row["path"]]) != row.get("sha256"):
            raise InstallError(f"hash mismatch: {row['path']}")
    return manifest


def inspect_package(package_path: Path, expected_target: str | None = None) -> dict[str, Any]:
    if not package_path.is_file():
        raise InstallError("package does not exist")
    with zipfile.ZipFile(package_path) as package:
        return _inspect_package(package, expected_target)


def _check_destination(project_dir: Path, destination: Path) -> None:
    for ancestor in (destination, *destination.parents):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise InstallError("destination contains a symlink or reparse point")
        if ancestor != destination and not stat.S_ISDIR(metadata.st_mode):
            raise InstallError("destination ancestor is not a directory")
    if not destination.resolve().is_relative_to(project_dir):
        raise InstallError("destination escapes the project")
    if os.path.lexists(destination):
        raise InstallError("installation would overwrite existing files")


def install_package(
    package_path: Path,
    project_dir: Path,
    *,
    expected_target: str | None = None,
) -> dict[str, Any]:
    project_dir = project_dir.absolute()
    # Inspect the original spelling before resolution can hide directory links.
    _check_destination(project_dir.resolve(), project_dir / MANIFEST_NAME)
    if not (project_dir / "project.godot").is_file():
        raise InstallError("destination is not a Redot project")
    project_dir = project_dir.resolve()
    with zipfile.ZipFile(package_path) as package:
        manifest = _inspect_package(package, expected_target)
        files = [row["path"] for row in manifest["files"]] + [MANIFEST_NAME]
        destinations = {name: project_dir.joinpath(*PurePosixPath(name).parts) for name in files}
        for destination in destinations.values():
            _check_destination(project_dir, destination)
        for name in sorted(files):
            destination = destinations[name]
            _check_destination(project_dir, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _check_destination(project_dir, destination)
            with destination.open("xb") as output:
                _stream_member(package, package.getinfo(name), output)
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
