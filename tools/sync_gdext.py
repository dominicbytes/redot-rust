# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Rehearse a gdext pin update without modifying source or lock files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import load_lock  # noqa: E402


class SyncError(RuntimeError):
    """Raised when a candidate checkout cannot support a dry-run audit."""


def _git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SyncError(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def candidate_contract(source: Path) -> dict[str, Any]:
    source = source.resolve()
    revision = _git(source, "rev-parse", "HEAD")
    status = _git(source, "status", "--short")
    godot_manifest = source / "godot" / "Cargo.toml"
    json_loader = source / "godot-bindings" / "src" / "godot_json.rs"
    if not godot_manifest.is_file() or not json_loader.is_file():
        raise SyncError("candidate checkout is missing godot manifest or JSON loader")
    manifest_text = godot_manifest.read_text(encoding="utf-8")
    loader_text = json_loader.read_text(encoding="utf-8")
    return {
        "revision": revision,
        "source_clean": status == "",
        "api_custom_json_feature": "api-custom-json" in manifest_text,
        "standard_header_loader": '"header"' in loader_text or ".header" in loader_text,
        "license_file": any(
            (source / name).is_file()
            for name in ("LICENSE", "LICENSE.txt", "License.txt")
        ),
        "cargo_lock": (source / "Cargo.lock").is_file(),
    }


def build_sync_report(
    lock: dict[str, Any],
    candidate: dict[str, Any],
    patch_files: list[str],
) -> dict[str, Any]:
    current = lock["gdext"]["revision"]
    same_revision = candidate["revision"] == current
    failures = []
    if not candidate["source_clean"]:
        failures.append("candidate checkout is dirty")
    if not candidate["api_custom_json_feature"]:
        failures.append("candidate removed api-custom-json")
    if not candidate["standard_header_loader"]:
        failures.append("candidate JSON loader no longer clearly uses the standard API header")
    if not candidate["license_file"]:
        failures.append("candidate license file is missing")
    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "dry_run": True,
        "applied": False,
        "current_revision": current,
        "candidate_revision": candidate["revision"],
        "revision_changed": not same_revision,
        "impact": {
            "api": "none" if same_revision else "regenerate and diff Redot API bindings",
            "dependencies": "none" if same_revision else "refresh Cargo.lock and license report",
            "license": "none" if same_revision else "review candidate LICENSE and dependency licenses",
            "patches": "none" if not patch_files else f"reapply/review {len(patch_files)} patch file(s)",
            "consumer": "none" if same_revision else "rerun Rapier G-02 against the candidate revision",
        },
        "candidate_contract": candidate,
        "patch_files": sorted(patch_files),
        "required_follow_up": []
        if same_revision
        else [
            "update the full gdext revision only after G-01 and G-02 pass",
            "run dependency/license diff and all desktop G-03 jobs",
        ],
        "failures": failures,
    }


def sync_report(lock_path: Path, candidate_source: Path, patches_dir: Path) -> dict[str, Any]:
    lock = load_lock(lock_path.resolve())
    candidate = candidate_contract(candidate_source)
    patch_files = []
    if patches_dir.is_dir():
        patch_files = [
            path.relative_to(patches_dir).as_posix()
            for path in patches_dir.rglob("*")
            if path.is_file()
        ]
    return build_sync_report(lock, candidate, patch_files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--candidate-source", required=True, type=Path)
    parser.add_argument("--patches-dir", type=Path, default=Path("patches"))
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = sync_report(args.lock, args.candidate_source, args.patches_dir)
    except (SyncError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
