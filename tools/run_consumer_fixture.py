# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Materialize and run the pinned Rapier consumer fixture in Redot."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.redot_api import sha256_file  # noqa: E402
from tools.run_fixture import _run_process, evaluate_initialization, evaluate_run  # noqa: E402
from tools.scan_redot_log import EvidenceSanitizer, scan_text  # noqa: E402


ENGINE_MARKER = "PHYSICS ENGINE 2D: Rapier2D v0.8.40"
RESULT_MARKER = "REDOT_RAPIER_CONSUMER status=PASS"
TYPED_SIGNAL_NOTICE = (
    "WARNING: godot-rust: TypedSignal connections are now auto-disconnected."
)


class ConsumerFixtureError(RuntimeError):
    """Raised when the consumer fixture cannot be materialized safely."""


def partition_findings(
    findings: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Separate the exact upstream hot-reload notice from blocking findings."""
    allowed: list[dict[str, object]] = []
    blocking: list[dict[str, object]] = []
    for finding in findings:
        if finding.get("line") == TYPED_SIGNAL_NOTICE:
            allowed.append(finding)
        else:
            blocking.append(finding)
    return blocking, allowed


def materialize_consumer_fixture(
    source_project: Path,
    output_project: Path,
    library: Path,
) -> dict[str, object]:
    source_project = source_project.resolve()
    output_project = output_project.resolve()
    library = library.resolve()
    required = ("project.godot", "rapier.gdextension", "main.gd", "main.tscn")
    missing = [name for name in required if not (source_project / name).is_file()]
    if missing:
        raise ConsumerFixtureError(f"consumer fixture is missing: {', '.join(missing)}")
    if not library.is_file():
        raise ConsumerFixtureError(f"consumer library is missing: {library}")
    if output_project == source_project or output_project.is_relative_to(source_project):
        raise ConsumerFixtureError("output must be outside the source fixture")
    if output_project.exists():
        raise ConsumerFixtureError(f"output already exists: {output_project}")

    shutil.copytree(
        source_project,
        output_project,
        ignore=shutil.ignore_patterns(".godot", ".results", "bin"),
    )
    binary_dir = output_project / "bin"
    binary_dir.mkdir()
    destination = binary_dir / "godot_rapier.dll"
    shutil.copy2(library, destination)
    return {
        "schema_version": 1,
        "library_sha256": sha256_file(destination),
        "library_size": destination.stat().st_size,
    }


def evaluate_consumer_result(
    returncode: int,
    timed_out: bool,
    result: dict[str, Any] | None,
    findings: list[dict[str, object]],
    log_text: str,
) -> list[str]:
    failures = evaluate_run(
        returncode, timed_out, result, findings, fixture_type="consumer"
    )
    if ENGINE_MARKER not in log_text:
        failures.append(f"runtime marker is missing: {ENGINE_MARKER}")
    if RESULT_MARKER not in log_text:
        failures.append(f"runtime marker is missing: {RESULT_MARKER}")
    return failures


def run_consumer_fixture(
    source_project: Path,
    output_project: Path,
    library: Path,
    redot_bin: Path,
    report_dir: Path,
    *,
    quit_after: int = 180,
    timeout: int = 90,
) -> dict[str, Any]:
    if quit_after < 2:
        raise ValueError("quit_after must be at least 2")
    selection = materialize_consumer_fixture(source_project, output_project, library)
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({
        output_project.resolve(): "<PROJECT_ROOT>",
        source_project.resolve(): "<SOURCE_PROJECT_ROOT>",
        redot_bin.resolve().parent: "<ENGINE_ROOT>",
        report_dir: "<EVIDENCE_ROOT>",
    })
    report_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_dump_editor(redot_bin)

    init_command = [
        str(resolved.editor),
        "--headless",
        "--audio-driver",
        "Dummy",
        "--editor",
        "--path",
        str(output_project.resolve()),
        "--quit-after",
        "120",
    ]
    init_code, init_timeout, init_stdout, init_stderr = _run_process(
        init_command, timeout
    )
    init_log = init_stdout + ("\n" if init_stdout and init_stderr else "") + init_stderr
    init_findings, init_allowed_findings = partition_findings(scan_text(init_log, sanitizer=sanitizer))
    init_log = sanitizer.text(init_log)
    sanitizer.write_text(report_dir / "redot-editor-init.log", init_log)
    init_failures = evaluate_initialization(init_code, init_timeout, init_findings)
    if ENGINE_MARKER not in init_log:
        init_failures.append(f"editor initialization marker is missing: {ENGINE_MARKER}")

    initialization = {
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
        "allowed_findings": init_allowed_findings,
        "engine_marker": ENGINE_MARKER in init_log,
    }
    if init_failures:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "selection": selection,
            "initialization": initialization,
            "command": None,
            "returncode": None,
            "timed_out": False,
            "result": None,
            "findings": [],
            "failures": init_failures,
        }
        report = sanitizer.finish(report_dir, "run-report.json", report)
        return report

    result_path = output_project.resolve() / ".results" / "consumer-result.json"
    command = [
        str(resolved.editor),
        "--headless",
        "--audio-driver",
        "Dummy",
        "--path",
        str(output_project.resolve()),
        "--fixed-fps",
        "60",
        "--quit-after",
        str(quit_after),
    ]
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    findings, allowed_findings = partition_findings(scan_text(combined, sanitizer=sanitizer))
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-runtime.log", combined)
    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    failures = evaluate_consumer_result(
        returncode, timed_out, result, findings, combined
    )
    report = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "selection": selection,
        "initialization": initialization,
        "command": [
            "<REDOT_EDITOR>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            "<PROJECT_ROOT>",
            "--fixed-fps",
            "60",
            "--quit-after",
            str(quit_after),
        ],
        "returncode": returncode,
        "timed_out": timed_out,
        "result": result,
        "findings": findings,
        "allowed_findings": allowed_findings,
        "markers": {
            ENGINE_MARKER: ENGINE_MARKER in combined,
            RESULT_MARKER: RESULT_MARKER in combined,
        },
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "run-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--output-project", required=True, type=Path)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--quit-after", type=int, default=180)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    try:
        report = run_consumer_fixture(
            args.source_project,
            args.output_project,
            args.library,
            args.redot_bin,
            args.report_dir,
            quit_after=args.quit_after,
            timeout=args.timeout,
        )
    except (ConsumerFixtureError, DumpError, OSError, ValueError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
