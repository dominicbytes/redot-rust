# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Verify that an exported Redot product contains the intended native library."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.package_release import TARGETS  # noqa: E402
from tools.redot_api import sha256_file  # noqa: E402
from tools.scan_redot_log import EvidenceSanitizer  # noqa: E402
from tools.verify_binary_deps import BinaryAuditError, audit_binary  # noqa: E402


class ExportProductError(RuntimeError):
    """Raised when an export archive is unsafe or structurally invalid."""


def _safe_archive_members(package: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = package.infolist()
    names: set[str] = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            raise ExportProductError(f"unsafe archive path: {name!r}")
        if name in names:
            raise ExportProductError(f"duplicate archive path: {name}")
        names.add(name)
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ExportProductError(f"archive symlink is forbidden: {name}")
    return members


def archive_library(archive: Path, expected_name: str) -> tuple[str, bytes, list[str]]:
    with zipfile.ZipFile(archive) as package:
        members = _safe_archive_members(package)
        native = sorted(
            member.filename
            for member in members
            if "redot_rust_smoke" in PurePosixPath(member.filename).name
            and PurePosixPath(member.filename).suffix == ".dylib"
        )
        matches = [name for name in native if PurePosixPath(name).name == expected_name]
        if len(matches) != 1:
            raise ExportProductError(
                f"expected one {expected_name} in export, found {len(matches)}"
            )
        expected_parent = PurePosixPath(matches[0]).parent
        if expected_parent.name != "Frameworks" or ".app" not in expected_parent.parts[-3]:
            raise ExportProductError(
                f"native library is outside the app Frameworks directory: {matches[0]}"
            )
        if native != matches:
            raise ExportProductError(f"unexpected native libraries in export: {native}")
        return matches[0], package.read(matches[0]), native


def verify_export(
    product: Path,
    target_name: str,
    profile: str,
    *,
    expected_library: Path | None = None,
) -> dict[str, Any]:
    product = product.resolve()
    if not product.is_file():
        raise ExportProductError(f"export product does not exist: {product}")
    if target_name not in TARGETS:
        raise ExportProductError(f"unsupported target: {target_name}")
    if profile not in ("debug", "release"):
        raise ExportProductError(f"unsupported profile: {profile}")
    target = TARGETS[target_name]
    expected_name = target[f"{profile}_name"]
    failures: list[str] = []

    if target_name == "macos-universal":
        member_name, library_data, native_candidates = archive_library(
            product, expected_name
        )
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / expected_name
            audit_path.write_bytes(library_data)
            binary_audit = audit_binary(
                audit_path,
                expected_architectures=set(target["architectures"]),
                expected_target=target_name,
            )
        library_hash = hashlib.sha256(library_data).hexdigest().upper()
        library_size = len(library_data)
    else:
        native_suffix = ".dll" if target_name == "windows-x86_64" else ".so"
        native_candidates = sorted(
            candidate.name
            for candidate in product.parent.iterdir()
            if candidate.is_file()
            and "redot_rust_smoke" in candidate.name
            and candidate.name.endswith(native_suffix)
        )
        if native_candidates != [expected_name]:
            raise ExportProductError(
                f"export native inventory does not match {expected_name}: {native_candidates}"
            )
        library_path = product.parent / expected_name
        member_name = expected_name
        binary_audit = audit_binary(
            library_path,
            expected_architectures=set(target["architectures"]),
            expected_target=target_name,
        )
        library_hash = sha256_file(library_path)
        library_size = library_path.stat().st_size

    failures.extend(binary_audit["failures"])
    expected = None
    if expected_library is not None:
        expected_library = expected_library.resolve()
        if not expected_library.is_file():
            raise ExportProductError(
                f"expected source library does not exist: {expected_library}"
            )
        expected = {
            "file": expected_library.name,
            "sha256": sha256_file(expected_library),
            "size": expected_library.stat().st_size,
        }
        if expected["sha256"] != library_hash:
            failures.append("exported library hash differs from the staged source library")

    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "target": target_name,
        "profile": profile,
        "product": {
            "file": product.name,
            "sha256": sha256_file(product),
            "size": product.stat().st_size,
        },
        "library": {
            "archive_path": member_name,
            "sha256": library_hash,
            "size": library_size,
        },
        "expected_library": expected,
        "native_candidates": native_candidates,
        "binary_audit": binary_audit,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", type=Path)
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--profile", required=True, choices=("debug", "release"))
    parser.add_argument("--expected-library", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_export(
            args.product,
            args.target,
            args.profile,
            expected_library=args.expected_library,
        )
    except (BinaryAuditError, ExportProductError, OSError, zipfile.BadZipFile) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    sanitizer = EvidenceSanitizer()
    report = sanitizer.data(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = sanitizer.finish(args.report.parent, args.report.name, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
