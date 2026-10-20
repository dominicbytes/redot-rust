# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate a locked Cargo dependency and license report."""

from __future__ import annotations

import argparse
import json
import os
import re
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


def linked_packages(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Follow normal target dependencies, excluding host-only procedural macros."""
    packages = {row["id"]: row for row in metadata["packages"]}
    nodes = {row["id"]: row for row in metadata["resolve"]["nodes"]}
    pending = [member for member in metadata["workspace_members"]
               if any("cdylib" in target["kind"] for target in packages[member]["targets"])]
    if not pending:
        raise DependencyReportError("no workspace cdylib found in Cargo metadata")
    visited = set()
    while pending:
        package_id = pending.pop()
        if package_id in visited:
            continue
        package = packages[package_id]
        if any("proc-macro" in target["kind"] for target in package["targets"]):
            continue
        visited.add(package_id)
        for dependency in nodes[package_id]["deps"]:
            if any(kind["kind"] is None for kind in dependency["dep_kinds"]):
                pending.append(dependency["pkg"])
    return sorted((packages[key] for key in visited), key=lambda row: (row["name"], row["version"]))


def cached_license_files(package: dict[str, Any]) -> dict[str, bytes]:
    """Read notices from this exact registry package or its git workspace root."""
    root = Path(package["manifest_path"]).parent
    directories = [root]
    if str(package.get("source", "")).startswith("git+"):
        for parent in [root, *root.parents]:
            if (parent / ".git").exists():
                if parent != root:
                    directories.append(parent)
                break
        else:
            raise DependencyReportError(f"cached git checkout missing for {package['name']}")
    files = {}
    for directory in directories:
        for path in sorted(directory.iterdir()):
            if path.is_file() and re.match(r"^(?:licen[cs]e|copying|notice|copyright)(?:[._-]|$)", path.name, re.I):
                files.setdefault(path.name, path.read_bytes())
    declared = package.get("license_file")
    if declared:
        path = root / declared
        files[path.name] = path.read_bytes()
    has_license_text = declared or any(
        re.match(r"^(?:licen[cs]e|copying)(?:[._-]|$)", name, re.I) for name in files
    )
    if not has_license_text or (not package.get("license") and not declared):
        raise DependencyReportError(f"cached license material missing for {package['name']}")
    return files


def third_party_bundle(
    repo_root: Path, targets: tuple[str, ...], cargo: str = "cargo"
) -> dict[str, bytes]:
    """Resolve the locked target graphs offline and ship their linked notices."""
    def read_metadata(target: str | None = None) -> dict[str, Any]:
        command = [str(cargo), "metadata", "--locked", "--offline", "--format-version", "1"]
        if target is not None:
            command.extend(["--filter-platform", target])
        completed = subprocess.run(
            command, cwd=repo_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, check=False,
        )
        if completed.returncode:
            raise DependencyReportError("offline Cargo metadata failed")
        return json.loads(completed.stdout)

    # The inventory covers every locked package, including other platforms;
    # only notice selection is restricted to the package's target graphs.
    inventory = normalize_metadata(read_metadata())["packages"]
    linked = {}
    for target in targets:
        metadata = read_metadata(target)
        for package in linked_packages(metadata):
            if package["id"] not in metadata["workspace_members"]:
                linked[(package["name"], package["version"], package["source"])] = package
    payload = {}
    linked_rows = []
    for key, package in sorted(linked.items()):
        directory = f"third-party/{package['name']}-{package['version']}"
        texts = cached_license_files(package)
        for name, data in texts.items():
            payload[f"{directory}/{name}"] = data
        linked_rows.append({
            "name": package["name"], "version": package["version"],
            "source": package["source"], "license": package.get("license"),
            "notices": [f"{directory}/{name}" for name in sorted(texts)],
        })
    report = {
        "schema_version": 1, "targets": list(targets),
        "scope": "normal target dependency closure; excludes build, dev and procedural macro dependencies",
        "linked_packages": linked_rows,
        "locked_packages": inventory,
    }
    payload["dependency-inventory.json"] = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return payload


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
        api_value = lock["binding_abi"].get("files", {}).get("extension_api", {}).get("path")
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
    report["cargo"] = lock["binding_abi"]["rust"]["cargo_version"]
    report["gdext_revision"] = lock["binding_abi"]["gdext"]["revision"]
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
