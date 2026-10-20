# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create a deterministic, target-specific Redot add-on archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dependency_report import DependencyReportError, third_party_bundle  # noqa: E402
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
        "cargo_targets": ("x86_64-pc-windows-msvc",),
        "debug_name": "redot_rust_smoke.windows.template_debug.x86_64.dll",
        "release_name": "redot_rust_smoke.windows.template_release.x86_64.dll",
        "debug_key": "windows.debug.x86_64",
        "release_key": "windows.release.x86_64",
    },
    "linux-x86_64": {
        "architectures": {"x86_64"},
        "cargo_targets": ("x86_64-unknown-linux-gnu",),
        "debug_name": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "release_name": "libredot_rust_smoke.linux.template_release.x86_64.so",
        "debug_key": "linux.debug.x86_64",
        "release_key": "linux.release.x86_64",
    },
    "macos-universal": {
        "architectures": {"arm64", "x86_64"},
        "cargo_targets": ("aarch64-apple-darwin", "x86_64-apple-darwin"),
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

SOURCE_REPOSITORY = "https://github.com/dominicbytes/redot-rust"
PRIVATE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+[A-Za-z0-9_. ~-]"
    r"|/(?:home|Users|tmp|private/tmp|var/folders|root|workspace|builds|__w|runner)/"
    r"|/github/workspace(?:/|$)|/opt/(?:hostedtoolcache|actions-runner)/"
    r"|/[a-z]/(?:Users|home|workspace)/"
    r"|\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_$.-]+)", re.I
)


class PackageError(RuntimeError):
    """Raised when a release archive would violate its frozen contract."""


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _gdextension(target: dict[str, Any], lock: dict[str, Any]) -> bytes:
    debug_path = ADDON_ROOT / "bin" / target["debug_name"]
    release_path = ADDON_ROOT / "bin" / target["release_name"]
    api = lock["godot_api"]
    minimum = f'{api["version_major"]}.{api["version_minor"]}'
    text = (
        '[configuration]\n\n'
        'entry_symbol = "gdext_rust_init"\n'
        f'compatibility_minimum = "{minimum}"\n'
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
    # Check every payload, including native libraries and extensionless license files.
    native_binary = path.suffix.casefold() in {".dll", ".so", ".dylib"}
    if native_binary:
        # Longer printable strings may follow arbitrary data. Short strings
        # need a NUL boundary to distinguish paths from instruction fragments.
        texts = []
        for match in re.finditer(rb"[\x20-\x7e]{4,}", data):
            if len(match.group()) >= 6 or match.start() == 0 or data[match.start() - 1] == 0:
                texts.append(match.group().decode("ascii"))
        for offset in (0, 1):
            decoded = data[offset:].decode("utf-16le", errors="ignore")
            for match in re.finditer(r"[\x20-\x7e]{4,}", decoded):
                if len(match.group()) >= 6 or match.start() == 0 or decoded[match.start() - 1] == "\0":
                    texts.append(match.group())
    else:
        texts = [data.decode("latin-1")]
        texts.extend(data[offset:].decode("utf-16le", errors="ignore") for offset in (0, 1))
    if path.suffix.casefold() == ".json":
        # JSON escaping must not hide a path from the publication gate.
        def strings(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, dict):
                return [text for pair in value.items() for item in pair for text in strings(item)]
            if isinstance(value, list):
                return [text for item in value for text in strings(item)]
            return []
        texts.extend(strings(json.loads(data)))
    if any(PRIVATE_PATH_PATTERN.search(text) for text in texts):
        # Do not copy the private value into diagnostics or uploaded reports.
        raise PackageError(f"private absolute path in {name}")


def create_package(
    repo_root: Path,
    target_name: str,
    debug_binary: Path,
    release_binary: Path,
    output_dir: Path,
    *,
    version: str = VERSION,
    cargo: str = "cargo",
    lock_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    if target_name not in TARGETS:
        raise PackageError(f"unsupported target: {target_name}")
    target = TARGETS[target_name]
    lock_path = lock_path.resolve() if lock_path else repo_root / "redot-api.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
        text=True, timeout=30, check=False,
    )
    revision = revision_result.stdout.strip()
    if revision_result.returncode or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise PackageError("cannot determine corresponding source revision")
    source_locator = {"repository": SOURCE_REPOSITORY, "revision": revision}

    audits = {
        "debug": audit_binary(
            debug_binary,
            expected_architectures=target["architectures"],
            expected_target=target_name,
        ),
        "release": audit_binary(
            release_binary,
            expected_architectures=target["architectures"],
            expected_target=target_name,
        ),
    }
    failed = [profile for profile, audit in audits.items() if audit["status"] != "PASS"]
    if failed:
        raise PackageError(f"binary audit failed for profile(s): {', '.join(failed)}")

    payload: dict[str, bytes] = {}
    for source_name, archive_name in DOCUMENTS.items():
        source = lock_path if source_name == "redot-api.lock.json" else repo_root / source_name
        if not source.is_file():
            raise PackageError(f"required package document is missing: {source_name}")
        payload[str(archive_name)] = source.read_bytes()
    for name, data in third_party_bundle(repo_root, target["cargo_targets"], cargo).items():
        payload[str(ADDON_ROOT / name)] = data
    payload[str(ADDON_ROOT / "SOURCE.json")] = (
        json.dumps(source_locator, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    payload[str(ADDON_ROOT / "redot_rust_smoke.gdextension")] = _gdextension(target, lock)
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
    manifest = {
        "schema_version": 1,
        "name": "redot-rust-compat",
        "version": version,
        "target": target_name,
        "source": source_locator,
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
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args(argv)
    try:
        report = create_package(
            args.repo_root,
            args.target,
            args.debug_binary,
            args.release_binary,
            args.output_dir,
            version=args.version,
            cargo=args.cargo,
            lock_path=args.lock,
        )
    except (BinaryAuditError, DependencyReportError, PackageError, OSError, ValueError,
            subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
