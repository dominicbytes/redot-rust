# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Run the Redot fixture with frame, wall-clock, assertion, and log gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.scan_redot_log import scan_text  # noqa: E402


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


def evaluate_run(
    returncode: int,
    timed_out: bool,
    result: dict[str, Any] | None,
    findings: list[dict[str, object]],
) -> list[str]:
    failures: list[str] = []
    if returncode != 0:
        failures.append(f"process exit code {returncode}")
    if timed_out:
        failures.append("wall-clock timeout")
    if result is None:
        failures.append("fixture result JSON is missing")
    else:
        if result.get("status") != "PASS":
            failures.append(f"fixture status is {result.get('status')!r}")
        frames = result.get("frames")
        if not isinstance(frames, int) or frames < 2:
            failures.append(f"fixture ran {frames} frame(s); at least 2 are required")
        assertions = result.get("assertions")
        if not isinstance(assertions, dict):
            failures.append("fixture assertions object is missing")
        else:
            for name, passed in assertions.items():
                if passed is not True:
                    failures.append(f"assertion failed: {name}")
    for finding in findings:
        failures.append(f"log {finding['severity']}: {finding['line']}")
    return failures


def _run_process(command: list[str], timeout: int) -> tuple[int, bool, str, str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, False, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return -1, True, stdout, stderr


def run_fixture(
    project_dir: Path,
    redot_bin: Path,
    report_dir: Path,
    *,
    quit_after: int = 120,
    timeout: int = 60,
    initialize: bool = True,
) -> dict[str, Any]:
    if quit_after < 2:
        raise ValueError("quit_after must be at least 2")
    project_dir = project_dir.resolve()
    report_dir = report_dir.resolve()
    result_path = project_dir / ".results" / "fixture-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists():
        result_path.unlink()
    report_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_dump_editor(redot_bin)
    initialization = None
    initialization_failures: list[str] = []
    if initialize:
        initialization_command = [
            str(resolved.editor),
            "--headless",
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
        (report_dir / "redot-editor-init.log").write_text(
            init_log, encoding="utf-8"
        )
        init_findings = scan_text(init_log)
        initialization_failures = evaluate_initialization(
            init_code, init_timeout, init_findings
        )
        initialization_failures.extend(
            evaluate_markers(init_log, EDITOR_MARKERS, "editor initialization")
        )
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
            "markers": {marker: marker in init_log for marker in EDITOR_MARKERS},
        }
        if initialization_failures:
            report = {
                "status": "FAIL",
                "initialization": initialization,
                "command": None,
                "returncode": None,
                "timed_out": False,
                "result": None,
                "findings": [],
                "failures": initialization_failures,
            }
            (report_dir / "run-report.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return report

    command = [
        str(resolved.editor),
        "--headless",
        "--path",
        str(project_dir),
        "--quit-after",
        str(quit_after),
    ]
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    (report_dir / "redot-runtime.log").write_text(combined, encoding="utf-8")
    (report_dir / "redot.log").write_text(combined, encoding="utf-8")

    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    findings = scan_text(combined)
    failures = evaluate_run(returncode, timed_out, result, findings)
    failures.extend(evaluate_markers(combined, RUNTIME_MARKERS, "runtime"))
    report = {
        "status": "PASS" if not failures else "FAIL",
        "initialization": initialization,
        "command": [
            "<REDOT_EDITOR>",
            "--headless",
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
    (report_dir / "run-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--quit-after", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--skip-initialize", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_fixture(
            args.project,
            args.redot_bin,
            args.report_dir,
            quit_after=args.quit_after,
            timeout=args.timeout,
            initialize=not args.skip_initialize,
        )
    except (DumpError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
