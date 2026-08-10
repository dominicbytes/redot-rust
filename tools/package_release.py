# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create a deterministic, target-specific Redot add-on archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import sha256_file  # noqa: E402
from tools.verify_binary_deps import (  # noqa: E402
    BinaryAuditError,
    audit_binary,
)


VERSION = "0.1.0+redot.26.2.1"
ADDON_ROOT = PurePosixPath("addons/redot-rust-compat")
MANIFEST_NAME = str(ADDON_ROOT / "package-manifest.json")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

TARGETS = {
    "windows-x86_64": {
        "architectures": {"x86_64"},
        "debug_name": "redot_rust_smoke.windows.template_debug.x86_64.dll",
        "release_name": "redot_rust_smoke.windows.template_release.x86_64.dll",
        "debug_key": "windows.debug.x86_64",
        "release_key": "windows.release.x86_64",
    },
    "linux-x86_64": {
        "architectures": {"x86_64"},
        "debug_name": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "release_name": "libredot_rust_smoke.linux.template_release.x86_64.so",
        "debug_key": "linux.debug.x86_64",
        "release_key": "linux.release.x86_64",
    },
    "macos-universal": {
        "architectures": {"arm64", "x86_64"},
        "debug_name": "libredot_rust_smoke.macos.template_debug.universal.dylib",
        "release_name": "libredot_rust_smoke.macos.template_release.universal.dylib",
        "debug_key": "macos.debug",
        "release_key": "macos.release",
    },
}

DOCUMENTS = {
    "LICENSE": ADDON_ROOT / "LICENSE",
    "NOTICE.md": ADDON_ROOT / "NOTICE.md",
    "docs/package-readme.md": ADDON_ROOT / "README.md",
    "UPSTREAM_LOCK.md": ADDON_ROOT / "UPSTREAM_LOCK.md",
    "redot-api.lock.json": ADDON_ROOT / "redot-api.lock.json",
    "docs/compatibility.md": ADDON_ROOT / "docs/compatibility.md",
    "docs/limitations.md": ADDON_ROOT / "docs/limitations.md",
}

PRIVATE_PATH_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?![\\/])|/(?:home|Users|tmp)/)"
    rb"[^\x00\r\n\"']*"
)


class PackageError(RuntimeError):
    """Raised when a release archive would violate its frozen contract."""


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _gdextension(target: dict[str, Any]) -> bytes:
    debug_path = ADDON_ROOT / "bin" / target["debug_name"]
    release_path = ADDON_ROOT / "bin" / target["release_name"]
    text = (
        '[configuration]\n\n'
        'entry_symbol = "gdext_rust_init"\n'
        'compatibility_minimum = "4.5"\n'
        'reloadable = false\n\n'
        '[libraries]\n\n'
        f'{target["debug_key"]} = "res://{debug_path}"\n'
        f'{target["release_key"]} = "res://{release_path}"\n'
    )
    return text.encode("utf-8")


def _validate_payload(name: str, data: bytes) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise PackageError(f"unsafe archive path: {name}")
    lowered = path.name.casefold()
    if lowered in {"extension_api.json", "gdextension_interface.h"}:
        raise PackageError(f"restricted generated artifact in package: {name}")
    if path.suffix.casefold() in {".exe", ".tpz", ".pdb", ".lib", ".exp"}:
        raise PackageError(f"restricted build/engine artifact in package: {name}")
    if path.suffix.casefold() in {".md", ".json", ".gdextension", ".txt"}:
        match = PRIVATE_PATH_PATTERN.search(data)
        if match:
            rendered = match.group(0).decode("utf-8", errors="replace")
            raise PackageError(f"private absolute path in {name}: {rendered}")


def create_package(
    repo_root: Path,
    target_name: str,
    debug_binary: Path,
    release_binary: Path,
    output_dir: Path,
    *,
    version: str = VERSION,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    if target_name not in TARGETS:
        raise PackageError(f"unsupported target: {target_name}")
    target = TARGETS[target_name]
    audits = {
        "debug": audit_binary(
            debug_binary,
            expected_architectures=target["architectures"],
        ),
        "release": audit_binary(
            release_binary,
            expected_architectures=target["architectures"],
        ),
    }
    failed = [profile for profile, audit in audits.items() if audit["status"] != "PASS"]
    if failed:
        raise PackageError(f"binary audit failed for profile(s): {', '.join(failed)}")

    payload: dict[str, bytes] = {}
    for source_name, archive_name in DOCUMENTS.items():
        source = repo_root / source_name
        if not source.is_file():
            raise PackageError(f"required package document is missing: {source_name}")
        payload[str(archive_name)] = source.read_bytes()
    payload[str(ADDON_ROOT / "redot_rust_smoke.gdextension")] = _gdextension(target)
    payload[str(ADDON_ROOT / "bin" / target["debug_name"])] = debug_binary.read_bytes()
    payload[str(ADDON_ROOT / "bin" / target["release_name"])] = release_binary.read_bytes()

    for name, data in payload.items():
        _validate_payload(name, data)
    files = [
        {
            "path": name,
            "sha256": hashlib.sha256(payload[name]).hexdigest().upper(),
            "size": len(payload[name]),
        }
        for name in sorted(payload)
    ]
    lock = json.loads((repo_root / "redot-api.lock.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "name": "redot-rust-compat",
        "version": version,
        "target": target_name,
        "redot": {
            "version": lock["redot"]["version_string"],
            "engine_commit": lock["redot"]["engine_commit"],
            "api_sha256": lock["files"]["extension_api"]["sha256"],
            "godot_api": (
                f'{lock["godot_api"]["version_major"]}.'
                f'{lock["godot_api"]["version_minor"]}.'
                f'{lock["godot_api"]["version_patch"]}'
            ),
            "precision": lock["redot"]["precision"],
        },
        "gdext": {
            "revision": lock["gdext"]["revision"],
            "crate_version": lock["gdext"]["crate_version"],
            "entry_symbol": "gdext_rust_init",
        },
        "profiles": {
            profile: {
                "library": target[f"{profile}_name"],
                "sha256": audit["sha256"],
                "architectures": audit["architectures"],
                "dependencies": audit["dependencies"],
            }
            for profile, audit in audits.items()
        },
        "files": files,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _validate_payload(MANIFEST_NAME, manifest_bytes)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_version = version.replace("+", "_")
    archive = output_dir / f"redot-rust-compat-{safe_version}-{target_name}.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as package:
        for name in sorted(payload):
            package.writestr(_zip_info(name), payload[name])
        package.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)

    archive_sha256 = sha256_file(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{archive_sha256}  {archive.name}\n", encoding="ascii")
    report = {
        "schema_version": 1,
        "status": "PASS",
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "archive_size": archive.stat().st_size,
        "checksum": checksum.name,
        "manifest": manifest,
    }
    report_path = output_dir / f"{archive.stem}.package-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--debug-binary", required=True, type=Path)
    parser.add_argument("--release-binary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", default=VERSION)
    args = parser.parse_args(argv)
    try:
        report = create_package(
            args.repo_root,
            args.target,
            args.debug_binary,
            args.release_binary,
            args.output_dir,
            version=args.version,
        )
    except (BinaryAuditError, PackageError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
