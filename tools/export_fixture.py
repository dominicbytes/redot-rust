# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Export and run a Redot compatibility fixture with an exact template."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dump_redot_api import DumpError, resolve_dump_editor  # noqa: E402
from tools.redot_api import DEFAULT_LOCK, binding_identity, engine_profile, load_lock, verify_engine, sha256_file
from tools.engine_gate import fixture_arguments, evaluate_fixture_identity, verify_runtime_version  # noqa: E402
from tools.run_fixture import _run_process, evaluate_run  # noqa: E402
from tools.scan_redot_log import EvidenceSanitizer, scan_text  # noqa: E402


TARGETS = {
    "windows-x86_64": {
        "platform": "Windows Desktop",
        "extension": ".exe",
        "template_names": {
            "debug": "windows_debug_x86_64.exe",
            "release": "windows_release_x86_64.exe",
        },
    },
    "linux-x86_64": {
        "platform": "Linux",
        "extension": "",
        "template_names": {
            "debug": "linux_debug.x86_64",
            "release": "linux_release.x86_64",
        },
    },
    "macos-universal": {
        "platform": "macOS",
        "extension": ".zip",
        "template_names": {"debug": "macos.zip", "release": "macos.zip"},
    },
}

RUNTIME_MARKERS = (
    "REDOT_RUST_INIT stage=Core",
    "REDOT_RUST_INIT stage=Servers",
    "REDOT_RUST_INIT stage=Scene",
    "REDOT_RUST_INIT stage=MainLoop",
)
RESULT_MARKERS = (
    "REDOT_RUST_RESULT status=PASS",
    "REDOT_RUST_CLEAN_RESULT status=PASS",
)
MACOS_ALLOWED_EXPORT_MESSAGES = (
    "Warning: Notarization is disabled. The exported project will be blocked by Gatekeeper",
    "Code signing is disabled. The exported project will not run on Macs with enabled Gatekeeper",
)


class ExportError(RuntimeError):
    """Raised when export inputs or products violate the target contract."""


def export_preset_text(
    target_name: str,
    profile: str,
    template: Path,
    *,
    template_reference: str | None = None,
) -> str:
    if target_name not in TARGETS:
        raise ExportError(f"unsupported target: {target_name}")
    if profile not in ("debug", "release"):
        raise ExportError(f"unsupported profile: {profile}")
    target = TARGETS[target_name]
    expected = target["template_names"][profile]
    if template.name != expected:
        raise ExportError(f"{target_name} {profile} requires {expected}, found {template.name}")
    template_value = template_reference or template.resolve().as_posix()
    if target_name == "macos-universal":
        # Redot's macOS preset validator checks both template variants even for a
        # single-profile command. The official macos.zip contains both.
        debug_template = release_template = template_value
    else:
        debug_template = template_value if profile == "debug" else ""
        release_template = template_value if profile == "release" else ""
    architecture = "universal" if target_name == "macos-universal" else "x86_64"
    etc2_astc = "true" if target_name == "macos-universal" else "false"
    macos_options = ""
    if target_name == "macos-universal":
        macos_options = '''
application/bundle_identifier="org.redotengine.redotrustcompat"
export/distribution_type=1
codesign/codesign=0
notarization/notarization=0
'''
    return f'''[preset.0]

name="Redot Rust Compat"
platform="{target["platform"]}"
runnable=true
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=".results/*"
export_path=""
patches=PackedStringArray()
patch_delta_encoding=false
patch_delta_compression_level_zstd=19
patch_delta_min_reduction=0.1
patch_delta_include_filters="*"
patch_delta_exclude_filters=""
encryption_include_filters=""
encryption_exclude_filters=""
seed=0
encrypt_pck=false
encrypt_directory=false
script_export_mode=2

[preset.0.options]

custom_template/debug="{debug_template}"
custom_template/release="{release_template}"
debug/export_console_wrapper=1
binary_format/embed_pck=true
binary_format/architecture="{architecture}"
texture_format/s3tc_bptc=true
texture_format/etc2_astc={etc2_astc}
shader_baker/enabled=false
codesign/enable=false
{macos_options}
'''


def _output_name(target_name: str, profile: str) -> str:
    suffix = TARGETS[target_name]["extension"]
    return f"redot-rust-smoke-{target_name}-{profile}{suffix}"


def export_fixture(
    project_dir: Path,
    redot_bin: Path,
    target_name: str,
    profile: str,
    template: Path,
    output_dir: Path,
    report_dir: Path,
    *,
    timeout: int = 180,
    docker_editor_image: str | None = None,
    lock_path: Path = DEFAULT_LOCK,
    engine_profile_name: str | None = None,
) -> dict[str, Any]:
    lock = load_lock(lock_path)
    selected, identity = engine_profile(lock, engine_profile_name)
    redot = identity["redot"]
    template_version = f'{redot["version_major"]}.{redot["version_minor"]}.{redot["version_status"]}'
    template = template.resolve()
    project_dir = project_dir.resolve()
    if not (project_dir / "project.godot").is_file():
        raise ExportError(f"not a Redot project: {project_dir}")
    if not template.is_file():
        raise ExportError(f"export template does not exist: {template}")
    template_reference = f"/templates/{template.name}" if docker_editor_image else None
    preset_text = export_preset_text(
        target_name, profile, template, template_reference=template_reference
    )
    (project_dir / "export_presets.cfg").write_text(preset_text, encoding="utf-8")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({
        project_dir: "<PROJECT_ROOT>",
        redot_bin.resolve().parent: "<ENGINE_ROOT>",
        template.resolve().parent: "<TEMPLATE_ROOT>",
        output_dir.resolve(): "<BUILD_ROOT>", report_dir: "<EVIDENCE_ROOT>",
    })
    report_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / _output_name(target_name, profile)
    if docker_editor_image:
        if not redot_bin.is_file():
            raise ExportError(f"Docker editor does not exist: {redot_bin}")
        version_command = ["docker", "run", "--rm", "-v", f"{redot_bin.resolve().parent}:/editor:ro", docker_editor_image, "sh", "-c", f"cp /editor/{redot_bin.name} /tmp/redot && chmod +x /tmp/redot && /tmp/redot --version"]
        runtime_engine = verify_engine(redot_bin, lock, selected, target="linux-x86_64", version_command=version_command)
        install_template = ""
        if target_name == "macos-universal":
            install_template = (
                "mkdir -p "
                f"/root/.local/share/redot/export_templates/{template_version} && "
                f"cp /templates/{template.name} "
                "/root/.local/share/redot/export_templates/"
                f"{template_version}/{template.name} && "
            )
        script = (
            f"cp /editor/{redot_bin.name} /tmp/redot && chmod +x /tmp/redot && "
            f"{install_template}"
            "/tmp/redot --headless --audio-driver Dummy --path /project --quit-after 120 --log-file /output/export-engine.log "
            f"{'--export-debug' if profile == 'debug' else '--export-release'} "
            f"'Redot Rust Compat' /output/{output.name}"
        )
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{redot_bin.resolve().parent}:/editor:ro",
            "-v",
            f"{template.resolve().parent}:/templates:ro",
            "-v",
            f"{project_dir}:/project",
            "-v",
            f"{output_dir}:/output",
            docker_editor_image,
            "sh",
            "-c",
            script,
        ]
        sanitized_command = [
            "docker",
            "run",
            "--rm",
            "<MOUNTS>",
            docker_editor_image,
            "<REDOT_EDITOR_EXPORT>",
        ]
    else:
        resolved = resolve_dump_editor(redot_bin)
        runtime_engine = verify_engine(resolved.editor, lock, selected)
        command = [
            str(resolved.editor),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            str(project_dir),
            "--quit-after", "120",
            "--export-debug" if profile == "debug" else "--export-release",
            "Redot Rust Compat",
            str(output),
        ]
        sanitized_command = [
            "<REDOT_EDITOR>",
            "--headless",
            "--audio-driver",
            "Dummy",
            "--path",
            "<PROJECT_ROOT>",
            "--quit-after", "120",
            "--export-debug" if profile == "debug" else "--export-release",
            "Redot Rust Compat",
            f"<OUTPUT>/{output.name}",
        ]
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    all_findings = scan_text(combined, sanitizer=sanitizer)
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-export.log", combined)
    allowed_findings = []
    findings = []
    for finding in all_findings:
        if target_name == "macos-universal" and any(
            str(finding["line"]).startswith(message)
            for message in MACOS_ALLOWED_EXPORT_MESSAGES
        ):
            allowed_findings.append(finding)
        else:
            findings.append(finding)
    failures: list[str] = []
    if returncode != 0:
        failures.append(f"export exit code {returncode}")
    if timed_out:
        failures.append("export wall-clock timeout")
    failures.extend(f"export log {row['severity']}: {row['line']}" for row in findings)
    if not output.is_file():
        failures.append(f"export product is missing: {output.name}")
    product = None
    if output.is_file():
        product = {
            "file": output.name,
            "sha256": sha256_file(output),
            "size": output.stat().st_size,
        }
    report = {
        "binding_abi": binding_identity(lock), "runtime_engine": runtime_engine,
        "schema_version": 2,
        "status": "PASS" if not failures else "FAIL",
        "target": target_name,
        "profile": profile,
        "template": {
            "file": template.name,
            "sha256": sha256_file(template),
            "size": template.stat().st_size,
        },
        "command": sanitized_command,
        "returncode": returncode,
        "timed_out": timed_out,
        "findings": findings,
        "allowed_findings": allowed_findings,
        "product": product,
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "export-report.json", report)
    return report


def _runtime_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "INVALID_JSON", "frames": 0, "assertions": {}}


def _macos_executable(archive: Path, unpack_dir: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        package.extractall(unpack_dir)
    candidates = sorted(unpack_dir.glob("*.app/Contents/MacOS/*"))
    candidates = [candidate for candidate in candidates if candidate.is_file()]
    if len(candidates) != 1:
        raise ExportError(f"expected one macOS executable, found {len(candidates)}")
    candidates[0].chmod(candidates[0].stat().st_mode | 0o111)
    return candidates[0]


def run_exported_fixture(
    product: Path,
    target_name: str,
    report_dir: Path,
    *,
    timeout: int = 120,
    docker_image: str = "rust:1.94-bookworm",
    fixture_type: str = "smoke",
    lock_path: Path = DEFAULT_LOCK,
    engine_profile_name: str | None = None,
) -> dict[str, Any]:
    lock = load_lock(lock_path)
    if fixture_type not in ("smoke", "clean-install"):
        raise ExportError(f"unsupported exported fixture type: {fixture_type}")
    product = product.resolve()
    if not product.is_file():
        raise ExportError(f"export product does not exist: {product}")
    if target_name not in TARGETS:
        raise ExportError(f"unsupported target: {target_name}")
    report_dir = report_dir.resolve()
    sanitizer = EvidenceSanitizer({product.parent: "<BUILD_ROOT>", report_dir: "<EVIDENCE_ROOT>"})
    report_dir.mkdir(parents=True, exist_ok=True)
    result_path = report_dir / "exported-fixture-result.json"
    if result_path.exists():
        result_path.unlink()

    if target_name == "windows-x86_64":
        console = product.with_name(product.stem + "_console.exe")
        executable = console if console.is_file() else product
        command = [
            str(executable),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--quit-after", "120",
            "--",
            f"--redot-rust-result={result_path}",
        ]
        sanitized = [
            "<EXPORTED_FIXTURE>", "--headless", "--audio-driver", "Dummy",
            "--quit-after", "120", "--", "--redot-rust-result=<RESULT>",
        ]
    elif target_name == "linux-x86_64":
        script = (
            "cp -R /export /tmp/redot-rust-export && "
            f"chmod +x /tmp/redot-rust-export/{product.name} && "
            f"/tmp/redot-rust-export/{product.name} --headless --audio-driver Dummy --quit-after 120 -- "
            "--redot-rust-result=/results/exported-fixture-result.json"
        )
        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{product.parent}:/export:ro",
            "-v",
            f"{report_dir}:/results",
            docker_image,
            "sh",
            "-c",
            script,
        ]
        sanitized = ["docker", "run", "--rm", "<MOUNTS>", docker_image, "<EXPORTED_FIXTURE>"]
    else:
        if sys.platform != "darwin":
            raise ExportError("macOS export execution requires a macOS host")
        unpack_dir = report_dir / "macos-export"
        if unpack_dir.exists():
            shutil.rmtree(unpack_dir)
        executable = _macos_executable(product, unpack_dir)
        command = [
            str(executable),
            "--headless",
            "--audio-driver",
            "Dummy",
            "--quit-after", "120",
            "--",
            f"--redot-rust-result={result_path}",
        ]
        sanitized = [
            "<EXPORTED_FIXTURE>", "--headless", "--audio-driver", "Dummy",
            "--quit-after", "120", "--", "--redot-rust-result=<RESULT>",
        ]

    if command[0] == "docker":
        version_command = command[:-1] + [f"cp -R /export /tmp/redot-rust-export && chmod +x /tmp/redot-rust-export/{product.name} && /tmp/redot-rust-export/{product.name} --version"]
        runtime_engine = verify_runtime_version(product, lock, engine_profile_name, target_name, version_command=version_command)
        command[-1] += " " + shlex.join(fixture_arguments(lock, runtime_engine))
    else:
        runtime_engine = verify_runtime_version(executable, lock, engine_profile_name, target_name)
        command += fixture_arguments(lock, runtime_engine)
    returncode, timed_out, stdout, stderr = _run_process(command, timeout)
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    findings = scan_text(combined, sanitizer=sanitizer)
    combined = sanitizer.text(combined)
    sanitizer.write_text(report_dir / "redot-exported-runtime.log", combined)
    result = _runtime_result(result_path)
    failures = evaluate_run(
        returncode, timed_out, result, findings, fixture_type=f"exported-{fixture_type}"
    )
    failures.extend(
        f"exported runtime marker is missing: {marker}"
        for marker in RUNTIME_MARKERS
        if marker not in combined
    )
    failures.extend(evaluate_fixture_identity(result, lock, runtime_engine))
    result_marker = RESULT_MARKERS[0 if fixture_type == "smoke" else 1]
    if result_marker not in combined:
        failures.append("exported runtime result marker is missing: " + result_marker)
    report = {
        "binding_abi": binding_identity(lock), "runtime_engine": runtime_engine,
        "schema_version": 2,
        "status": "PASS" if not failures else "FAIL",
        "target": target_name,
        "command": sanitized,
        "returncode": returncode,
        "timed_out": timed_out,
        "result": result,
        "findings": findings,
        "markers": {
            marker: marker in combined for marker in RUNTIME_MARKERS + RESULT_MARKERS
        },
        "failures": failures,
    }
    report = sanitizer.finish(report_dir, "exported-run-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--project", required=True, type=Path)
    export_parser.add_argument("--redot-bin", required=True, type=Path)
    export_parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    export_parser.add_argument("--profile", required=True, choices=("debug", "release"))
    export_parser.add_argument("--template", required=True, type=Path)
    export_parser.add_argument("--output-dir", required=True, type=Path)
    export_parser.add_argument("--report-dir", required=True, type=Path)
    export_parser.add_argument("--timeout", type=int, default=180)
    export_parser.add_argument("--docker-editor-image")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--product", required=True, type=Path)
    run_parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    run_parser.add_argument("--report-dir", required=True, type=Path)
    run_parser.add_argument("--timeout", type=int, default=120)
    run_parser.add_argument("--fixture-type", choices=("smoke", "clean-install"), default="smoke")
    run_parser.add_argument("--docker-image", default="rust:1.94-bookworm")
    for subparser in (export_parser, run_parser):
        subparser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
        subparser.add_argument("--engine-profile")
    args = parser.parse_args(argv)
    try:
        if args.action == "export":
            report = export_fixture(
                args.project,
                args.redot_bin,
                args.target,
                args.profile,
                args.template,
                args.output_dir,
                args.report_dir,
                timeout=args.timeout,
                lock_path=args.lock, engine_profile_name=args.engine_profile,
                docker_editor_image=args.docker_editor_image,
            )
        else:
            report = run_exported_fixture(
                args.product,
                args.target,
                args.report_dir,
                timeout=args.timeout,
                lock_path=args.lock, engine_profile_name=args.engine_profile,
                docker_image=args.docker_image,
                fixture_type=args.fixture_type,
            )
    except (DumpError, ExportError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
