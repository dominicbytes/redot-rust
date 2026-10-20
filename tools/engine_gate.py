# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Bounded native engine capture and exact, visible editor-import exceptions."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from tools.redot_api import IdentityError, binding_identity, engine_profile
from tools.scan_redot_log import EvidenceSanitizer, scan_text

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_process(command: list[str], timeout: int) -> tuple[int, bool, str, str]:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    command = list(command)
    # Native logger paths must be absolute. Console and logger output are both scanned.
    with tempfile.TemporaryDirectory(prefix="engine-log-") as directory:
        log = Path(directory).resolve() / "engine.log"
        if "--headless" in command and command[0] != "docker":
            if "--quit-after" not in command or int(command[command.index("--quit-after") + 1]) < 2:
                raise ValueError("engine invocation requires --quit-after >= 2")
            separator = command.index("--") if "--" in command else len(command)
            command[separator:separator] = ["--log-file", str(log)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=timeout,
                                       check=False)
            code, timed_out, stdout, stderr = completed.returncode, False, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as error:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            code, timed_out, stdout, stderr = -1, True, decode(error.stdout), decode(error.stderr)
        logger = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        console = ANSI.sub("", stdout + "\n" + stderr)
        logger = ANSI.sub("", logger)
        # Avoid copying the same messages twice, but preserve logger-only diagnostics.
        missing = [line for line in logger.splitlines() if line and line not in console.splitlines()]
        stdout = ANSI.sub("", stdout) + ("\n" + "\n".join(missing) if missing else "")
        return code, timed_out, stdout, ANSI.sub("", stderr)


def fixture_arguments(lock: dict, runtime: dict) -> list[str]:
    return ["--redot-rust-expected-binding=" + json.dumps(binding_identity(lock), separators=(",", ":")),
            "--redot-rust-expected-engine=" + json.dumps(runtime["identity"], separators=(",", ":"))]


def evaluate_fixture_identity(result: Any, lock: dict, runtime: dict, *, require_binding: bool = True) -> list[str]:
    if not isinstance(result, dict):
        return ["fixture identity is missing"]
    failures = []
    if require_binding and result.get("binding_abi") != binding_identity(lock):
        failures.append("fixture compile-time binding ABI differs from the lock")
    actual = result.get("runtime_engine")
    if (not isinstance(actual, dict) or not isinstance(actual.get("redot"), dict)
            or not isinstance(actual.get("godot_api"), dict)):
        return failures + ["fixture runtime identity is missing or malformed"]
    redot = runtime["identity"]["redot"]
    api = runtime["identity"]["godot_api"]
    for key in ("major", "minor", "patch", "status", "build"):
        if actual.get("redot", {}).get(key) != redot["version_" + key]:
            failures.append("fixture runtime Redot identity differs from selected profile")
        if actual.get("godot_api", {}).get(key) != api["version_" + key]:
            failures.append("fixture runtime Godot API identity differs from selected profile")
    if actual.get("redot", {}).get("hash") != redot["engine_commit"]:
        failures.append("fixture runtime engine commit differs from selected profile")
    return failures


def clear_import_caches(project: Path) -> None:
    for name in ("extension_list.cfg", "global_script_class_cache.cfg"):
        (project / ".godot" / name).unlink(missing_ok=True)


def evaluate_import_exception(text: str, code: int, timed_out: bool, *, lock: dict,
                              runtime: dict, project: Path, markers: tuple[str, ...],
                              context: str, command: list[str]) -> dict | None:
    """Never used for a runtime or export; exact engine verification precedes this call."""
    name, identity = engine_profile(lock, runtime["profile"])
    relative = identity.get("import_exception")
    if not relative or context != "editor-import" or runtime["target"] != "windows-x86_64":
        return None
    # Restrict even direct callers to editor initialization with no user payload.
    if ("--editor" not in command or "--path" not in command or "--" in command
            or any(arg in ("--script", "-s", "--scene") or arg.startswith("--export")
                   or arg.endswith((".gd", ".tscn")) for arg in command)):
        return None
    config = json.loads((Path(__file__).resolve().parents[1] / relative).read_text())
    if (runtime["version_string"] != identity["redot"]["version_string"]
            or runtime["binary_sha256"] not in identity["assets"][runtime["target"]]["binaries"].values()
            or timed_out or code != 0):
        return None
    text = ANSI.sub("", text)
    diagnostics = scan_text(text)
    lines = [row["line"].strip() for row in diagnostics]
    allowed = set(config["diagnostics"])
    if not lines or set(lines) != allowed or any(line not in allowed for line in lines):
        return None
    if any(marker not in text for marker in markers):
        return None
    if not all(re.search(r"\[ DONE \]\s+" + marker, text)
               for marker in ("first_scan_filesystem", "loading_editor_layout")):
        return None
    try:
        cache = project / ".godot"
        extensions = (cache / "extension_list.cfg").read_text().splitlines()
        classes = (cache / "global_script_class_cache.cfg").read_text()
    except OSError:
        return None
    descriptors = ["res://" + path.relative_to(project).as_posix()
                   for path in project.rglob("*.gdextension") if ".godot" not in path.parts]
    if not descriptors or not all(path in extensions for path in descriptors) or "list=" not in classes.replace(" ", ""):
        return None
    return {"profile": name, "scope": context, "reason": config["reason"],
            "diagnostics": config["diagnostics"], "returncode": code,
            "completed_stages": True, "fresh_caches_verified": True,
            "removal_condition": config["removal_condition"]}


def verify_runtime_version(binary: Path, lock: dict, selected: str | None, target: str,
                           *, version_command: list[str] | None = None, timeout: int = 30) -> dict:
    from tools.redot_api import sha256_file
    name, identity = engine_profile(lock, selected)
    result = subprocess.run(version_command or [str(binary), "--version"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if result.returncode or result.stdout.strip() != identity["redot"]["version_string"]:
        raise IdentityError("runtime --version differs from selected engine profile")
    return {"profile": name, "identity": identity, "target": target,
            "version_string": result.stdout.strip(), "binary_sha256": sha256_file(binary)}
