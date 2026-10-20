# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Run the Redot fixture with frame, wall-clock, assertion, and log gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import DEFAULT_LOCK, binding_identity, load_lock, verify_engine, sha256_file
from tools.engine_gate import (run_process, fixture_arguments, evaluate_fixture_identity,
                               clear_import_caches, evaluate_import_exception)
from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.scan_redot_log import EvidenceSanitizer, scan_text  # noqa: E402


EDITOR_MARKERS = (
    "REDOT_RUST_INIT stage=Core",
    "REDOT_RUST_INIT stage=Servers",
    "REDOT_RUST_INIT stage=Scene",
    "REDOT_RUST_INIT stage=Editor",
    "REDOT_RUST_INIT stage=MainLoop",
    "REDOT_RUST_EDITOR_TOOL status=PASS",
)
RUNTIME_MARKERS = (
    "REDOT_RUST_INIT stage=Core",
    "REDOT_RUST_INIT stage=Servers",
    "REDOT_RUST_INIT stage=Scene",
    "REDOT_RUST_INIT stage=MainLoop",
    "REDOT_RUST_SMOKE lifecycle=ready",
    "REDOT_RUST_RESULT status=PASS",
)


def evaluate_markers(text: str, markers: tuple[str, ...], context: str) -> list[str]:
    return [
        f"{context} marker is missing: {marker}"
        for marker in markers
        if marker not in text
    ]


def evaluate_initialization(
    returncode: int,
    timed_out: bool,
    findings: list[dict[str, object]],
) -> list[str]:
    failures: list[str] = []
    if returncode != 0:
        failures.append(f"editor initialization exit code {returncode}")
    if timed_out:
        failures.append("editor initialization wall-clock timeout")
    for finding in findings:
        failures.append(
            f"editor initialization log {finding['severity']}: {finding['line']}"
        )
    return failures


# Exit zero also signals Redot's frame cap; require the fixture's own quit path.
RUNTIME_SUCCESS_EXIT = 42
ASSERTION_SCHEMA_VERSION = 2
SMOKE_ASSERTIONS = frozenset({
    "class_visible", "resource_class_visible", "tool_class_visible", "method",
    "property", "signal", "lifecycle_ready", "identity", "build_profile",
    "init_stages", "enum", "array", "dictionary", "variant",
    "runtime_engine", "rust_to_gdscript_callable", "resource_apply", "resource_inspector",
    "resource_round_trip", "editor_tool", "worker_started", "worker_handoff",
})
CLEAN_ASSERTIONS = frozenset({
    "class_visible", "instance", "method", "signal", "identity", "lifecycle",
    "build_profile", "runtime_engine",
})
CONSUMER_ASSERTIONS = frozenset({
    "runtime_engine", "project_selects_rapier", "server_class_registered", "factory_class_registered",
    "active_server_is_rapier", "body_rid_valid", "body_uses_active_space",
    "server_reported_active_objects", "rigid_body_fell", "rigid_body_resting_on_floor",
})
EXPECTED_ASSERTIONS = {
    "smoke": SMOKE_ASSERTIONS, "clean-install": CLEAN_ASSERTIONS,
    "consumer": CONSUMER_ASSERTIONS, "exported-smoke": SMOKE_ASSERTIONS,
    "exported-clean-install": CLEAN_ASSERTIONS,
}


def evaluate_result(
    result: object, fixture_type: str, *, expected_dimension: str = "2d",
) -> list[str]:
    failures: list[str] = []
    if not isinstance(result, dict):
        return ["fixture result JSON is missing or is not an object"]
    if result.get("status") != "PASS":
        failures.append(f"fixture status is {result.get('status')!r}")
    if type(result.get("assertion_schema_version")) is not int or result.get(
        "assertion_schema_version"
    ) != ASSERTION_SCHEMA_VERSION:
        failures.append("unsupported or missing assertion schema version")
    expected_type = fixture_type.removeprefix("exported-")
    if result.get("fixture_type") != expected_type:
        failures.append(f"fixture type does not match {expected_type}")
    frames = result.get("frames")
    if type(frames) is not int or frames < 2:
        failures.append(f"fixture ran {frames} frame(s); at least 2 are required")
    assertions = result.get("assertions")
    if not isinstance(assertions, dict):
        failures.append("fixture assertions object is missing")
    else:
        expected = EXPECTED_ASSERTIONS[fixture_type]
        missing = expected - assertions.keys()
        unknown = assertions.keys() - expected
        if missing:
            failures.append("missing assertions: " + ", ".join(sorted(missing)))
        if unknown:
            failures.append("unknown assertions: " + ", ".join(sorted(unknown)))
        for name, passed in assertions.items():
            if passed is not True:
                failures.append(f"assertion failed: {name}")
    if fixture_type == "consumer":
        if result.get("dimension") != expected_dimension:
            failures.append(f"consumer dimension does not match {expected_dimension}")
        if result.get("active_server_class") != f"RapierPhysicsServer{expected_dimension.upper()}":
            failures.append("consumer active server class does not match expected dimension")
    return failures


def evaluate_run(
    returncode: int,
    timed_out: bool,
    result: dict[str, Any] | None,
    findings: list[dict[str, object]],
    *,
    fixture_type: str = "smoke",
    expected_dimension: str = "2d",
) -> list[str]:
    failures: list[str] = []
    if returncode != RUNTIME_SUCCESS_EXIT:
        failures.append(f"process exit code {returncode}; fixture completion requires {RUNTIME_SUCCESS_EXIT}")
    if timed_out:
        failures.append("wall-clock timeout")
    failures.extend(evaluate_result(result, fixture_type, expected_dimension=expected_dimension))
    for finding in findings:
        failures.append(f"log {finding['severity']}: {finding['line']}")
    return failures


_run_process = run_process

def run_fixture(
    project_dir: Path,
    redot_bin: Path,
    report_dir: Path,
    *,
    quit_after: int = 120,
    timeout: int = 60,
    initialize: bool = True,
    initialize_only: bool = False,
    lock_path: Path = DEFAULT_LOCK,
    engine_profile_name: str | None = None,
) -> dict[str, Any]:
    if initialize_only and not initialize:
        raise ValueError("initialize_only requires initialization")
    if quit_after < 2:
        raise ValueError("quit_after must be at least 2")
    project_dir = project_dir.resolve()
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({
        project_dir: "<PROJECT_ROOT>",
        redot_bin.resolve().parent: "<ENGINE_ROOT>",
        report_dir: "<EVIDENCE_ROOT>",
    })
    result_path = project_dir / ".results" / "fixture-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists():
        result_path.unlink()
    report_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_dump_editor(redot_bin)
    lock = load_lock(lock_path)
    runtime = verify_engine(resolved.editor, lock, engine_profile_name, timeout=timeout)
    identities = {"binding_abi": binding_identity(lock), "runtime_engine": runtime}
    initialization = None
    initialization_failures: list[str] = []
    if initialize:
        clear_import_caches(project_dir)
        initialization_command = [
            str(resolved.editor),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--editor",
            "--path",
            str(project_dir),
            "--quit-after",
            "120",
        ]
        init_code, init_timeout, init_stdout, init_stderr = _run_process(
            initialization_command, timeout
        )
        init_log = init_stdout + (
            "\n" if init_stdout and init_stderr else ""
        ) + init_stderr
        exception = evaluate_import_exception(init_log, init_code, init_timeout, lock=lock,
                                              runtime=runtime, project=project_dir,
                                              markers=EDITOR_MARKERS, context="editor-import",
                                              command=initialization_command)
        init_findings = scan_text(init_log, sanitizer=sanitizer)
        init_log = sanitizer.text(init_log)
        sanitizer.write_text(report_dir / "redot-editor-init.log", init_log)
        initialization_failures = evaluate_initialization(
            0 if exception else init_code, init_timeout, [] if exception else init_findings
        )
        initialization_failures.extend(
            evaluate_markers(init_log, EDITOR_MARKERS, "editor initialization")
        )
        initialization = {
            "engine_import_exception": exception,
            "command": [
                "<REDOT_EDITOR>",
                "--headless",
                "--audio-driver",
                "Dummy",
                "--editor",
                "--path",
                "<PROJECT_ROOT>",
                "--quit-after",
                "120",
            ],
            "returncode": init_code,
            "timed_out": init_timeout,
            "findings": init_findings,
            "markers": {marker: marker in init_log for marker in EDITOR_MARKERS},
        }
        if initialization_failures:
            report = {
                **identities,
                "schema_version": 2, "scope": "editor-import",
                "status": "FAIL",
                "initialization": initialization,
                "command": None,
                "returncode": None,
                "timed_out": False,
                "result": None,
                "findings": [],
                "failures": initialization_failures,
            }
            report = sanitizer.finish(report_dir, "run-report.json", report)
            return report

    if initialize_only:
        cache_hashes = {name: sha256_file(project_dir / ".godot" / name)
                        for name in ("extension_list.cfg", "global_script_class_cache.cfg")
                        if (project_dir / ".godot" / name).is_file()}
        report = {**identities, "schema_version": 2, "scope": "editor-import", "status": "PASS", "initialization": initialization,
                  "import_cache_sha256": cache_hashes, "failures": []}
        return sanitizer.finish(report_dir, "run-report.json", report)

    command = [
        str(resolved.editor),
        "--headless",
        "--audio-driver",
        "Dummy",
        "--path",
        str(project_dir),
        "--quit-after",
        str(quit_after),
    ]
    command += ["--", *fixture_arguments(lock, runtime)]
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    findings = scan_text(combined, sanitizer=sanitizer)
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-runtime.log", combined)
    sanitizer.write_text(report_dir / "redot.log", combined)

    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    failures = evaluate_run(returncode, timed_out, result, findings)
    failures.extend(evaluate_markers(combined, RUNTIME_MARKERS, "runtime"))
    failures.extend(evaluate_fixture_identity(result, lock, runtime))
    report = {
        **identities,
        "schema_version": 2, "scope": "editor-and-runtime" if initialize else "runtime-only",
        "status": "PASS" if not failures else "FAIL",
        "initialization": initialization,
        "command": [
            "<REDOT_EDITOR>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            "<PROJECT_ROOT>",
            "--quit-after",
            str(quit_after),
        ],
        "returncode": returncode,
        "timed_out": timed_out,
        "result": result,
        "findings": findings,
        "markers": {marker: marker in combined for marker in RUNTIME_MARKERS},
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "run-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--quit-after", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--skip-initialize", action="store_true")
    parser.add_argument("--initialize-only", action="store_true")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--engine-profile")
    args = parser.parse_args(argv)
    try:
        report = run_fixture(
            args.project,
            args.redot_bin,
            args.report_dir,
            quit_after=args.quit_after,
            timeout=args.timeout,
            initialize=not args.skip_initialize,
            initialize_only=args.initialize_only,
            lock_path=args.lock, engine_profile_name=args.engine_profile,
        )
    except (DumpError, OSError, ValueError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
