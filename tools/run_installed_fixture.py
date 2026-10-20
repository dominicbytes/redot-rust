# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Run a clean-installed target package using a matching Redot binary."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.export_fixture import RUNTIME_MARKERS  # noqa: E402
from tools.run_fixture import (  # noqa: E402
    EDITOR_MARKERS,
    _run_process,
    evaluate_initialization,
    evaluate_markers,
    evaluate_run,
)
from tools.scan_redot_log import EvidenceSanitizer, scan_text  # noqa: E402


class InstalledFixtureError(RuntimeError):
    """Raised when target runtime inputs do not match the package contract."""


def run_installed_fixture(
    project: Path,
    target: str,
    report_dir: Path,
    *,
    redot_bin: Path | None = None,
    linux_template: Path | None = None,
    docker_image: str = "rust:1.94-bookworm",
    timeout: int = 120,
) -> dict[str, Any]:
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise InstalledFixtureError(f"not a Redot project: {project}")
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({
        project: "<PROJECT_ROOT>", report_dir: "<EVIDENCE_ROOT>",
        **({redot_bin.resolve().parent: "<ENGINE_ROOT>"} if redot_bin else {}),
    })
    report_dir.mkdir(parents=True, exist_ok=True)
    result_path = report_dir / "installed-fixture-result.json"
    if result_path.exists():
        result_path.unlink()

    native_host = (
        (target == "windows-x86_64" and os.name == "nt")
        or (target == "linux-x86_64" and sys.platform.startswith("linux"))
        or (target == "macos-universal" and sys.platform == "darwin")
    )
    initialization_command: list[str] | None = None
    sanitized_initialization: list[str] | None = None
    if native_host and redot_bin is not None:
        editor = resolve_dump_editor(redot_bin).editor
        initialization_command = [
            str(editor),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--editor",
            "--path",
            str(project),
            "--quit-after",
            "120",
        ]
        sanitized_initialization = [
            "<REDOT_EDITOR>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--editor",
            "--path",
            "<PROJECT_ROOT>",
            "--quit-after",
            "120",
        ]
        command = [
            str(editor),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            str(project),
            "--quit-after",
            "120",
            "--",
            f"--redot-rust-result={result_path}",
        ]
        sanitized = [
            "<REDOT_EDITOR>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            "<PROJECT_ROOT>",
            "--quit-after",
            "120",
            "--",
            "--redot-rust-result=<RESULT>",
        ]
    elif target == "windows-x86_64":
        if redot_bin is None:
            raise InstalledFixtureError("--redot-bin is required for Windows")
        raise InstalledFixtureError("Windows installed runs require a Windows host")
    elif target == "linux-x86_64":
        if redot_bin is None:
            raise InstalledFixtureError(
                "--redot-bin is required to initialize a clean Linux project"
            )
        if linux_template is None or not linux_template.is_file():
            raise InstalledFixtureError("a matching --linux-template is required")
        linux_editor = resolve_dump_editor(redot_bin).editor
        init_script = (
            f"cp /editor/{linux_editor.name} /tmp/redot-editor && "
            "chmod +x /tmp/redot-editor && "
            "/tmp/redot-editor --headless --audio-driver Dummy --editor --path /project --quit-after 120"
        )
        initialization_command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{linux_editor.parent}:/editor:ro",
            "-v",
            f"{project}:/project",
            docker_image,
            "sh",
            "-c",
            init_script,
        ]
        sanitized_initialization = [
            "docker",
            "run",
            "--rm",
            "<MOUNTS>",
            docker_image,
            "<REDOT_EDITOR_INITIALIZATION>",
        ]
        script = (
            f"cp /templates/{linux_template.name} /tmp/redot && "
            "chmod +x /tmp/redot && "
            "/tmp/redot --headless --audio-driver Dummy --path /project --quit-after 120 -- "
            "--redot-rust-result=/results/installed-fixture-result.json"
        )
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{linux_template.resolve().parent}:/templates:ro",
            "-v",
            f"{project}:/project:ro",
            "-v",
            f"{report_dir}:/results",
            docker_image,
            "sh",
            "-c",
            script,
        ]
        sanitized = ["docker", "run", "--rm", "<MOUNTS>", docker_image, "<REDOT_TEMPLATE>"]
    elif target == "macos-universal":
        raise InstalledFixtureError("--redot-bin is required for macOS")
    else:
        raise InstalledFixtureError(f"unsupported installed target: {target}")

    if initialization_command is None or sanitized_initialization is None:
        raise InstalledFixtureError("clean project initialization command is missing")
    init_code, init_timed_out, init_stdout, init_stderr = _run_process(
        initialization_command, timeout
    )
    init_combined = init_stdout + (
        "\n" if init_stdout and init_stderr else ""
    ) + init_stderr
    init_findings = scan_text(init_combined, sanitizer=sanitizer)
    init_combined = sanitizer.text(init_combined)
    sanitizer.write_text(report_dir / "redot-installed-editor-init.log", init_combined)
    init_failures = evaluate_initialization(
        init_code, init_timed_out, init_findings
    )
    init_failures.extend(
        evaluate_markers(init_combined, EDITOR_MARKERS, "editor initialization")
    )
    initialization = {
        "command": sanitized_initialization,
        "returncode": init_code,
        "timed_out": init_timed_out,
        "findings": init_findings,
        "markers": {marker: marker in init_combined for marker in EDITOR_MARKERS},
    }
    if init_failures:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "target": target,
            "initialization": initialization,
            "runtime": None,
            "failures": init_failures,
        }
        report = sanitizer.finish(report_dir, "installed-run-report.json", report)
        return report

    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    findings = scan_text(combined, sanitizer=sanitizer)
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-installed-runtime.log", combined)
    result = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {"status": "INVALID_JSON", "frames": 0, "assertions": {}}
    failures = evaluate_run(returncode, timed_out, result, findings, fixture_type="clean-install")
    failures.extend(
        f"installed runtime marker is missing: {marker}"
        for marker in RUNTIME_MARKERS
        if marker not in combined
    )
    clean_marker = "REDOT_RUST_CLEAN_RESULT status=PASS"
    if clean_marker not in combined:
        failures.append(f"installed runtime marker is missing: {clean_marker}")
    report = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "target": target,
        "initialization": initialization,
        "command": sanitized,
        "returncode": returncode,
        "timed_out": timed_out,
        "result": result,
        "findings": findings,
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "installed-run-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--redot-bin", type=Path)
    parser.add_argument("--linux-template", type=Path)
    parser.add_argument("--docker-image", default="rust:1.94-bookworm")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        report = run_installed_fixture(
            args.project,
            args.target,
            args.report_dir,
            redot_bin=args.redot_bin,
            linux_template=args.linux_template,
            docker_image=args.docker_image,
            timeout=args.timeout,
        )
    except (DumpError, InstalledFixtureError, OSError, ValueError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
