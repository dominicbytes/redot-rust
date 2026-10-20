# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Run a profile-selected Linux fixture with an official Redot template in Docker."""

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
from tools.materialize_fixture import MaterializeError, materialize_fixture  # noqa: E402
from tools.redot_api import sha256_file  # noqa: E402
from tools.run_fixture import (  # noqa: E402
    EDITOR_MARKERS,
    RUNTIME_MARKERS,
    _run_process,
    evaluate_initialization,
    evaluate_markers,
    evaluate_run,
)
from tools.scan_redot_log import EvidenceSanitizer, scan_text  # noqa: E402


PROFILES = {
    "debug": {
        "library": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "template": "linux_debug.x86_64",
    },
    "release": {
        "library": "libredot_rust_smoke.linux.template_release.x86_64.so",
        "template": "linux_release.x86_64",
    },
}


class LinuxFixtureError(RuntimeError):
    """Raised when Linux fixture inputs are inconsistent."""


def _docker_run(command: list[str], timeout: int) -> tuple[int, bool, str, str]:
    return _run_process(command, timeout)


def run_linux_fixture(
    source_project: Path,
    output_project: Path,
    profile: str,
    redot_bin: Path,
    template: Path,
    report_dir: Path,
    *,
    docker_image: str = "rust:1.94-bookworm",
    quit_after: int = 120,
    timeout: int = 120,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise LinuxFixtureError(f"unsupported profile: {profile}")
    if quit_after < 2:
        raise ValueError("quit_after must be at least 2")
    expected_template = PROFILES[profile]["template"]
    if template.name != expected_template:
        raise LinuxFixtureError(
            f"{profile} requires template {expected_template}, found {template.name}"
        )
    if not template.is_file():
        raise LinuxFixtureError(f"Redot Linux template is missing: {template}")

    materialize_fixture(
        source_project, output_project, profile, target_name="linux-x86_64"
    )
    output_project = output_project.resolve()
    result_path = output_project / ".results" / "fixture-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({
        output_project.resolve(): "<PROJECT_ROOT>",
        source_project.resolve(): "<SOURCE_PROJECT_ROOT>",
        redot_bin.resolve().parent: "<ENGINE_ROOT>",
        report_dir: "<EVIDENCE_ROOT>",
    })
    report_dir.mkdir(parents=True, exist_ok=True)

    library = output_project / "bin" / PROFILES[profile]["library"]
    if not library.is_file():
        raise LinuxFixtureError(f"Linux fixture library is missing: {library}")
    selection = {
        "schema_version": 1,
        "profile": profile,
        "library": library.name,
        "library_sha256": sha256_file(library),
        "library_size": library.stat().st_size,
        "template": template.name,
        "template_sha256": sha256_file(template),
        "template_size": template.stat().st_size,
        "docker_image": docker_image,
    }
    (output_project / "bin" / "profile-selection.json").write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    resolved = resolve_dump_editor(redot_bin)
    init_command = [
        str(resolved.editor),
        "--headless",
        "--audio-driver",
        "Dummy",
        "--editor",
        "--path",
        str(output_project),
        "--quit-after",
        "120",
    ]
    init_code, init_timeout, init_stdout, init_stderr = _run_process(
        init_command, timeout
    )
    init_log = init_stdout + ("\n" if init_stdout and init_stderr else "") + init_stderr
    init_findings = scan_text(init_log, sanitizer=sanitizer)
    init_log = sanitizer.text(init_log)
    sanitizer.write_text(report_dir / "redot-editor-init.log", init_log)
    init_failures = evaluate_initialization(init_code, init_timeout, init_findings)
    init_failures.extend(
        evaluate_markers(init_log, EDITOR_MARKERS, "editor initialization")
    )
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
        "markers": {marker: marker in init_log for marker in EDITOR_MARKERS},
    }
    if init_failures:
        report = {
            "status": "FAIL",
            "selection": selection,
            "initialization": initialization,
            "runtime": None,
            "failures": init_failures,
        }
        report = sanitizer.finish(report_dir, "run-report.json", report)
        return report

    template_dir = str(template.resolve().parent)
    script = (
        f"cp /templates/{template.name} /tmp/redot && "
        "chmod +x /tmp/redot && "
        f"/tmp/redot --headless --audio-driver Dummy --path /fixture --quit-after {quit_after}"
    )
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{template_dir}:/templates:ro",
        "-v",
        f"{output_project}:/fixture",
        docker_image,
        "sh",
        "-c",
        script,
    ]
    returncode, timed_out, stdout, stderr = _docker_run(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    findings = scan_text(combined, sanitizer=sanitizer)
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-runtime.log", combined)
    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    failures = evaluate_run(returncode, timed_out, result, findings)
    failures.extend(evaluate_markers(combined, RUNTIME_MARKERS, "runtime"))
    runtime = {
        "command": [
            "docker",
            "run",
            "--rm",
            "<MOUNTS>",
            docker_image,
            "<REDOT_TEMPLATE>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            "/fixture",
            "--quit-after",
            str(quit_after),
        ],
        "returncode": returncode,
        "timed_out": timed_out,
        "result": result,
        "findings": findings,
        "markers": {marker: marker in combined for marker in RUNTIME_MARKERS},
    }
    report = {
        "status": "PASS" if not failures else "FAIL",
        "selection": selection,
        "initialization": initialization,
        "runtime": runtime,
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "run-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--output-project", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--docker-image", default="rust:1.94-bookworm")
    parser.add_argument("--quit-after", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        report = run_linux_fixture(
            args.source_project,
            args.output_project,
            args.profile,
            args.redot_bin,
            args.template,
            args.report_dir,
            docker_image=args.docker_image,
            quit_after=args.quit_after,
            timeout=args.timeout,
        )
    except (DumpError, LinuxFixtureError, MaterializeError, OSError, ValueError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
