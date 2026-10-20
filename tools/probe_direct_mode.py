# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Reproduce and classify gdext direct-binary mode against locked Redot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.configure_cargo import GDEXT_ENV_KEYS  # noqa: E402
from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.redot_api import IdentityError, binding_abi, load_lock, verify_engine, sha256_file  # noqa: E402


EXPECTED_FAILURE = "Only Godot major version 4 is supported"


def evaluate_direct_probe(
    returncode: int, combined_output: str, expected_version: str
) -> tuple[str, list[str]]:
    failures: list[str] = []
    if returncode == 0:
        failures.append("direct mode unexpectedly succeeded")
    if EXPECTED_FAILURE not in combined_output:
        failures.append("expected Godot-major rejection was not observed")
    if not expected_version or expected_version not in combined_output:
        failures.append("locked Redot product version was not present in the rejection")
    return ("PASS" if not failures else "FAIL", failures)


def sanitize_output(text: str, replacements: dict[Path, str]) -> str:
    result = text
    for path, token in replacements.items():
        result = result.replace(str(path), token)
    result = result.replace(str(Path.home()), "<USER_HOME>")
    return result


def run_probe(
    lock_path: Path,
    redot_bin: Path,
    cargo_bin: Path,
    manifest_path: Path,
    target_dir: Path,
    report_dir: Path,
    timeout: int,
    engine_profile_name: str | None = None,
) -> dict[str, object]:
    lock_path = lock_path.resolve()
    manifest_path = manifest_path.resolve()
    cargo_bin = cargo_bin.resolve()
    target_dir = target_dir.resolve()
    report_dir = report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    lock = load_lock(lock_path)
    resolved = resolve_dump_editor(redot_bin)
    runtime_engine = verify_engine(resolved.editor, lock, engine_profile_name, timeout=timeout)
    actual_hash = runtime_engine["binary_sha256"]
    if not cargo_bin.is_file():
        raise IdentityError(f"cargo binary does not exist: {cargo_bin}")
    if not manifest_path.is_file():
        raise IdentityError(f"direct probe manifest does not exist: {manifest_path}")
    if not (manifest_path.parent / "Cargo.lock").is_file():
        raise IdentityError("direct probe Cargo.lock is missing")

    environment = dict(os.environ)
    for key in GDEXT_ENV_KEYS:
        environment.pop(key, None)
    environment["GDRUST_GODOT_BIN"] = str(resolved.editor)

    command = [
        str(cargo_bin),
        "check",
        "--manifest-path",
        str(manifest_path),
        "--locked",
        "--target-dir",
        str(target_dir),
    ]
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        timed_out = False
        combined = completed.stdout + (
            "\n" if completed.stdout and completed.stderr else ""
        ) + completed.stderr
    except subprocess.TimeoutExpired as error:
        returncode = -1
        timed_out = True
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        combined = stdout + ("\n" if stdout and stderr else "") + stderr

    sanitized = sanitize_output(
        combined,
        {
            resolved.editor: "<REDOT_EDITOR>",
            cargo_bin: "<CARGO>",
            manifest_path.parent: "<DIRECT_PROBE>",
            target_dir: "<TARGET_DIR>",
            lock_path.parent: "<REPOSITORY>",
        },
    )
    (report_dir / "cargo-direct-mode.log").write_text(sanitized, encoding="utf-8")
    status, failures = evaluate_direct_probe(
        returncode, sanitized, runtime_engine["version_string"]
    )
    if timed_out:
        failures.append("direct-mode probe timed out")
        status = "FAIL"

    report = {
        "status": status,
        "disposition": "WAIVED" if status == "PASS" else "UNRESOLVED",
        "expected_mode": "api-custom direct binary",
        "selected_mode": "api-custom-json",
        "returncode": returncode,
        "timed_out": timed_out,
        "expected_failure": EXPECTED_FAILURE,
        "redot_version": runtime_engine["version_string"],
        "redot_editor_sha256": actual_hash,
        "gdext_revision": binding_abi(lock)["gdext"]["revision"],
        "failures": failures,
    }
    (report_dir / "direct-mode-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--cargo-bin", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--engine-profile")
    args = parser.parse_args(argv)
    try:
        report = run_probe(
            args.lock,
            args.redot_bin,
            args.cargo_bin,
            args.manifest,
            args.target_dir,
            args.report_dir,
            args.timeout, args.engine_profile,
        )
    except (DumpError, IdentityError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
