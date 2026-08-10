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
from tools.run_fixture import _run_process, evaluate_initialization  # noqa: E402
from tools.scan_redot_log import scan_text  # noqa: E402


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
    failures: list[str] = []
    if returncode != 0:
        failures.append(f"process exit code {returncode}")
    if timed_out:
        failures.append("wall-clock timeout")
    if ENGINE_MARKER not in log_text:
        failures.append(f"runtime marker is missing: {ENGINE_MARKER}")
    if RESULT_MARKER not in log_text:
        failures.append(f"runtime marker is missing: {RESULT_MARKER}")
    if result is None:
        failures.append("consumer result JSON is missing")
    else:
        if result.get("status") != "PASS":
            failures.append(f"consumer status is {result.get('status')!r}")
        frames = result.get("frames")
        if not isinstance(frames, int) or frames < 2:
            failures.append(f"consumer ran {frames} frame(s); at least 2 are required")
        assertions = result.get("assertions")
        if not isinstance(assertions, dict):
            failures.append("consumer assertions object is missing")
        else:
            for name, passed in assertions.items():
                if passed is not True:
                    failures.append(f"assertion failed: {name}")
    for finding in findings:
        failures.append(f"log {finding['severity']}: {finding['line']}")
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
    report_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_dump_editor(redot_bin)

    init_command = [
        str(resolved.editor),
        "--headless",
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
    (report_dir / "redot-editor-init.log").write_text(init_log, encoding="utf-8")
    init_findings, init_allowed_findings = partition_findings(scan_text(init_log))
    init_failures = evaluate_initialization(init_code, init_timeout, init_findings)
    if ENGINE_MARKER not in init_log:
        init_failures.append(f"editor initialization marker is missing: {ENGINE_MARKER}")

    initialization = {
        "command": [
            "<REDOT_EDITOR>",
            "--headless",
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
        (report_dir / "run-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report

    result_path = output_project.resolve() / ".results" / "consumer-result.json"
    command = [
        str(resolved.editor),
        "--headless",
        "--path",
        str(output_project.resolve()),
        "--fixed-fps",
        "60",
        "--quit-after",
        str(quit_after),
    ]
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    (report_dir / "redot-runtime.log").write_text(combined, encoding="utf-8")
    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    findings, allowed_findings = partition_findings(scan_text(combined))
    failures = evaluate_consumer_result(
        returncode, timed_out, result, findings, combined
    )
    report = {
        "status": "PASS" if not failures else "FAIL",
        "selection": selection,
        "initialization": initialization,
        "command": [
            "<REDOT_EDITOR>",
            "--headless",
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
    (report_dir / "run-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
