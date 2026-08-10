# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate a locked Cargo dependency and license report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.configure_cargo import prepare_environment  # noqa: E402
from tools.redot_api import IdentityError, load_lock, resolve_locked_path  # noqa: E402


class DependencyReportError(RuntimeError):
    """Raised when Cargo metadata is incomplete or cannot be reproduced."""


def normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    packages = []
    missing_licenses = []
    package_names_by_id = {
        package.get("id"): f"{package.get('name')} {package.get('version')}"
        for package in metadata.get("packages", [])
    }
    for package in sorted(
        metadata.get("packages", []), key=lambda row: (row.get("name", ""), row.get("version", ""))
    ):
        license_value = package.get("license") or package.get("license_file")
        if not license_value:
            missing_licenses.append(f"{package.get('name')} {package.get('version')}")
        source = package.get("source") or "workspace/path"
        packages.append(
            {
                "name": package.get("name"),
                "version": package.get("version"),
                "license": license_value,
                "source": source,
            }
        )
    return {
        "schema_version": 1,
        "status": "PASS" if not missing_licenses else "FAIL",
        "package_count": len(packages),
        "workspace_members": sorted(
            package_names_by_id.get(member, "unknown workspace member")
            for member in metadata.get("workspace_members", [])
        ),
        "packages": packages,
        "missing_licenses": missing_licenses,
    }


def dependency_report(
    repo_root: Path,
    cargo: Path,
    lock_path: Path,
    api_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    lock_path = lock_path.resolve()
    lock = load_lock(lock_path)
    if api_path is None:
        api_value = lock.get("files", {}).get("extension_api", {}).get("path")
        if not isinstance(api_value, str):
            raise DependencyReportError("files.extension_api.path is missing from lock")
        api_path = resolve_locked_path(lock_path, api_value)
    environment, identity = prepare_environment(lock_path, api_path, None, os.environ)
    completed = subprocess.run(
        [str(cargo), "metadata", "--locked", "--format-version", "1"],
        cwd=repo_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise DependencyReportError(
            f"cargo metadata failed with exit code {completed.returncode}: {completed.stderr.strip()}"
        )
    metadata = json.loads(completed.stdout)
    report = normalize_metadata(metadata)
    report["identity"] = {
        "redot_product": identity["redot_product"],
        "godot_api": identity["godot_api"],
        "api_sha256": identity["api_sha256"],
    }
    report["cargo"] = lock["rust"]["cargo_version"]
    report["gdext_revision"] = lock["gdext"]["revision"]
    return report


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Cargo dependency and license report",
        "",
        f"Status: **{report['status']}**",
        "",
        f"Locked packages: {report['package_count']}",
        "",
        "| Package | Version | License | Source |",
        "|---|---:|---|---|",
    ]
    for package in report["packages"]:
        source = str(package["source"]).replace("|", "\\|")
        lines.append(
            f"| {package['name']} | {package['version']} | "
            f"{package['license'] or 'MISSING'} | {source} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--cargo", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--api", type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = dependency_report(args.repo_root, args.cargo, args.lock, args.api)
    except (
        DependencyReportError,
        IdentityError,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
