# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Verify the pinned Rapier consumer graph, source boundary, DLLs, and runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import sha256_file  # noqa: E402
from tools.run_fixture import RUNTIME_SUCCESS_EXIT, evaluate_result  # noqa: E402
from tools.scan_redot_log import EvidenceSanitizer  # noqa: E402


GODOT_PACKAGES = {
    "godot",
    "godot-bindings",
    "godot-cell",
    "godot-codegen",
    "godot-core",
    "godot-ffi",
    "godot-macros",
}
RAPIER_ENTRY_PATTERN = re.compile(r"\brapier_[23]d_init\b")


class ConsumerProbeError(RuntimeError):
    """Raised when the consumer proof input is malformed or incomplete."""


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ConsumerProbeError(f"command failed ({completed.returncode}): {detail}")
    return completed.stdout


def _git(source_repo: Path, *args: str) -> str:
    normalized = source_repo.resolve().as_posix()
    return _run(
        [
            "git",
            "-c",
            f"safe.directory={normalized}",
            "-C",
            str(source_repo.resolve()),
            *args,
        ]
    ).strip()


def payload_hash(root: Path) -> tuple[str, int]:
    """Hash all Rust sources plus manifests, excluding the intentionally updated lock."""
    candidates = [root / "Cargo.toml", root / "rust-toolchain.toml"]
    candidates.extend(sorted((root / "src").rglob("*.rs")))
    missing = [path for path in candidates if not path.is_file()]
    if missing:
        raise ConsumerProbeError(f"consumer payload is missing: {missing[0].name}")
    digest = hashlib.sha256()
    for path in candidates:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest().upper(), len(candidates)


def inspect_gdext_lock(
    lock_path: Path, revision: str, version: str,
    repository: str = "https://github.com/godot-rust/gdext",
) -> dict[str, Any]:
    cargo_lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    expected_source = urlsplit(repository)
    observed: dict[str, list[dict[str, str]]] = {}
    failures: list[str] = []
    for package in cargo_lock.get("package", []):
        name = package.get("name")
        if name not in GODOT_PACKAGES:
            continue
        observed.setdefault(name, []).append({
            "version": package.get("version", ""), "source": package.get("source", ""),
        })
    missing = sorted(GODOT_PACKAGES - observed.keys())
    if missing:
        failures.append(f"gdext packages are missing: {', '.join(missing)}")
    for name, occurrences in sorted(observed.items()):
        if len(occurrences) != 1:
            failures.append(f"{name} has {len(occurrences)} occurrences; expected exactly one")
        for package in occurrences:
            if package["version"] != version:
                failures.append(f"{name} version is {package['version']}, expected {version}")
            source = package["source"]
            try:
                parsed = urlsplit(source.removeprefix("git+"))
            except ValueError:
                failures.append(f"{name} source URL is invalid")
                continue
            if (
                not source.startswith("git+")
                or (parsed.scheme, parsed.netloc, parsed.path) != (
                    expected_source.scheme, expected_source.netloc, expected_source.path
                )
                or parse_qs(parsed.query, keep_blank_values=True) != {"rev": [revision]}
                or parsed.fragment != revision
            ):
                failures.append(f"{name} source does not match the pinned gdext repository and revision")
    return {"packages": observed, "failures": failures}


def inspect_exports(dumpbin: Path, library: Path) -> dict[str, Any]:
    output = _run([str(dumpbin.resolve()), "/exports", str(library.resolve())])
    entry_symbols = sorted(set(RAPIER_ENTRY_PATTERN.findall(output)))
    return {
        "sha256": sha256_file(library),
        "size": library.stat().st_size,
        "rapier_entry_symbols": entry_symbols,
        "has_2d_entry": "rapier_2d_init" in entry_symbols,
        "has_3d_entry": "rapier_3d_init" in entry_symbols,
    }


def inspect_runtime(report_path: Path, expected_sha: str, expected_dimension: str = "2d") -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        report = {}
    result = report.get("result")
    failures = evaluate_result(result, "consumer", expected_dimension=expected_dimension)
    result = result if isinstance(result, dict) else {}
    assertions = result.get("assertions")
    assertions = assertions if isinstance(assertions, dict) else {}
    if type(report.get("schema_version")) is not int or report.get("schema_version") != 1:
        failures.append("unsupported or missing consumer runtime report schema")
    if report.get("returncode") != RUNTIME_SUCCESS_EXIT or report.get("timed_out") is not False:
        failures.append("runtime report does not prove fixture completion")
    if report.get("status") != "PASS":
        failures.append(f"runtime status is {report.get('status')!r}")
    if report.get("failures") != []:
        failures.append("runtime report contains failures")
    if report.get("findings") != []:
        failures.append("runtime report contains blocking findings")
    selection = report.get("selection")
    if not isinstance(selection, dict) or selection.get("library_sha256") != expected_sha:
        failures.append("runtime library hash does not match inspected DLL")
    initialization = report.get("initialization")
    initialization = initialization if isinstance(initialization, dict) else {}
    return EvidenceSanitizer().data({
        "status": report.get("status"),
        "frames": result.get("frames"),
        "assertions_passed": sum(value is True for value in assertions.values()),
        "assertions_total": len(assertions),
        "active_server_class": result.get("active_server_class"),
        "peak_active_objects": result.get("peak_active_objects"),
        "body_position_y": result.get("body_position_y"),
        "allowed_findings": initialization.get(
            "allowed_findings", []
        ),
        "failures": failures,
    })


def probe_consumer(
    source_repo: Path,
    consumer_tree: Path,
    consumer_lock: Path,
    redot_lock: Path,
    debug_library: Path,
    release_library: Path,
    debug_runtime_report: Path,
    release_runtime_report: Path,
    dumpbin: Path,
) -> dict[str, Any]:
    inputs = json.loads(consumer_lock.read_text(encoding="utf-8"))
    redot = json.loads(redot_lock.read_text(encoding="utf-8"))
    expected_commit = inputs["commit"]
    expected_version = inputs["package_version"]
    gdext_revision = redot["gdext"]["revision"]
    gdext_version = redot["gdext"]["crate_version"]
    failures: list[str] = []

    source_head_before = _git(source_repo, "rev-parse", "HEAD")
    source_status_before = _git(source_repo, "status", "--porcelain")
    source_hash, source_file_count = payload_hash(source_repo.resolve())
    consumer_hash, consumer_file_count = payload_hash(consumer_tree.resolve())
    manifest = tomllib.loads((consumer_tree / "Cargo.toml").read_text(encoding="utf-8"))
    package_version = manifest.get("package", {}).get("version")
    if source_head_before != expected_commit:
        failures.append(f"source HEAD is {source_head_before}, expected {expected_commit}")
    if source_status_before:
        failures.append("source checkout was dirty before inspection")
    if package_version != expected_version:
        failures.append(
            f"consumer package version is {package_version}, expected {expected_version}"
        )
    if source_hash != consumer_hash or source_file_count != consumer_file_count:
        failures.append("generated consumer Rust payload differs from the pinned source")

    graph = inspect_gdext_lock(
        consumer_tree / "Cargo.lock", gdext_revision, gdext_version,
        redot["gdext"]["repository"],
    )
    failures.extend(graph["failures"])
    binaries = {
        "debug": inspect_exports(dumpbin, debug_library),
        "release": inspect_exports(dumpbin, release_library),
    }
    for profile, binary in binaries.items():
        if binary["rapier_entry_symbols"] != ["rapier_2d_init"]:
            failures.append(
                f"{profile} Rapier entry symbols are {binary['rapier_entry_symbols']}"
            )

    runtimes = {
        "debug": inspect_runtime(
            debug_runtime_report, binaries["debug"]["sha256"], inputs["dimension"]
        ),
        "release": inspect_runtime(
            release_runtime_report, binaries["release"]["sha256"], inputs["dimension"]
        ),
    }
    for profile, runtime in runtimes.items():
        failures.extend(f"{profile}: {failure}" for failure in runtime["failures"])

    source_head_after = _git(source_repo, "rev-parse", "HEAD")
    source_status_after = _git(source_repo, "status", "--porcelain")
    if source_head_after != source_head_before:
        failures.append("source HEAD changed during inspection")
    if source_status_after:
        failures.append("source checkout was dirty after inspection")

    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "consumer": {
            "repository": inputs["repository"],
            "tag": inputs["tag"],
            "commit": expected_commit,
            "package_version": package_version,
            "cargo_features": inputs["cargo_features"],
            "final_crate_cfg": inputs["final_crate_cfg"],
        },
        "source_boundary": {
            "clean_before": not bool(source_status_before),
            "clean_after": not bool(source_status_after),
            "payload_sha256": source_hash,
            "payload_file_count": source_file_count,
            "generated_payload_matches": source_hash == consumer_hash,
        },
        "gdext": {
            "revision": gdext_revision,
            "crate_version": gdext_version,
            "packages": graph["packages"],
        },
        "binaries": binaries,
        "runtimes": runtimes,
        "failures": failures,
    }


def write_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sanitizer = EvidenceSanitizer()
    report.update(sanitizer.data(report))
    lines = [
        "# Rapier consumer proof",
        "",
        f"Status: **{report['status']}**",
        "",
        f"- Rapier: `{report['consumer']['tag']}` / `{report['consumer']['commit']}`",
        f"- Source payload unchanged: `{report['source_boundary']['generated_payload_matches']}`",
        f"- Source clean before/after: `{report['source_boundary']['clean_before']}` / `{report['source_boundary']['clean_after']}`",
        f"- gdext: `{report['gdext']['revision']}` ({report['gdext']['crate_version']})",
        f"- Debug entry symbols: `{', '.join(report['binaries']['debug']['rapier_entry_symbols'])}`",
        f"- Release entry symbols: `{', '.join(report['binaries']['release']['rapier_entry_symbols'])}`",
        f"- Debug runtime: `{report['runtimes']['debug']['status']}`",
        f"- Release runtime: `{report['runtimes']['release']['status']}`",
        "",
    ]
    if report["failures"]:
        lines.append("## Failures")
        lines.append("")
        lines.extend(f"- {failure}" for failure in report["failures"])
        lines.append("")
    sanitizer.write_text(output_dir / "consumer-report.md", "\n".join(lines))
    report.update(sanitizer.finish(output_dir, "consumer-report.json", report))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", required=True, type=Path)
    parser.add_argument("--consumer-tree", required=True, type=Path)
    parser.add_argument("--consumer-lock", default="consumer/rapier.lock.json", type=Path)
    parser.add_argument("--redot-lock", default="redot-api.lock.json", type=Path)
    parser.add_argument("--debug-library", required=True, type=Path)
    parser.add_argument("--release-library", required=True, type=Path)
    parser.add_argument("--debug-runtime-report", required=True, type=Path)
    parser.add_argument("--release-runtime-report", required=True, type=Path)
    parser.add_argument("--dumpbin", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = probe_consumer(
            args.source_repo,
            args.consumer_tree,
            args.consumer_lock,
            args.redot_lock,
            args.debug_library,
            args.release_library,
            args.debug_runtime_report,
            args.release_runtime_report,
            args.dumpbin,
        )
        write_report(report, args.output_dir)
    except (ConsumerProbeError, KeyError, OSError, ValueError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
