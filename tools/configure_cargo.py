# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Validate a Redot API lock, then run Cargo with gdext JSON-mode variables."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import (  # noqa: E402
    IdentityError,
    inspect_api,
    load_lock,
    resolve_locked_path,
    sha256_file,
)


GDEXT_ENV_KEYS = (
    "GDRUST_GODOT_API_JSON",
    "GDRUST_GODOT_INTERFACE_JSON",
    "GDRUST_GODOT_BIN",
    "GODOT4_GDEXTENSION_JSON",
    "GODOT4_BIN",
)


def remap_environment(
    base_environment: Mapping[str, str], repo_root: Path
) -> dict[str, str]:
    """Keep Cargo's flag precedence and encode paths containing spaces safely."""
    environment = dict(base_environment)
    encoded = environment.get("CARGO_ENCODED_RUSTFLAGS")
    flags = encoded.split("\x1f") if encoded else []
    if encoded is None:
        # Cargo splits RUSTFLAGS on whitespace, without shell quote processing.
        flags = environment.get("RUSTFLAGS", "").split()
    roots = {
        repo_root.resolve(): "__source",
        Path(environment.get("CARGO_HOME", Path.home() / ".cargo")).resolve(): "__cargo",
        Path(environment.get("RUSTUP_HOME", Path.home() / ".rustup")).resolve(): "__rustup",
        Path(environment.get("CARGO_TARGET_DIR", repo_root / "target")).resolve(): "__target",
    }
    # Rust uses the last matching mapping; give the most specific root priority.
    for root, replacement in sorted(roots.items(), key=lambda item: len(str(item[0]))):
        for spelling in sorted({str(root), root.as_posix()}):
            flags.append(f"--remap-path-prefix={spelling}={replacement}")
    target = environment.get("CARGO_BUILD_TARGET", "")
    if target.endswith("-msvc") or (not target and sys.platform == "win32"):
        flags.extend(["-C", "link-arg=/PDBALTPATH:%_PDB%"])
    environment["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    return environment


def prepare_environment(
    lock_path: Path,
    api_path: Path,
    interface_json: Path | None,
    base_environment: Mapping[str, str],
) -> tuple[dict[str, str], dict]:
    lock_path = lock_path.resolve()
    api_path = api_path.resolve()
    lock = load_lock(lock_path)
    report = inspect_api(api_path, lock)

    environment = remap_environment(base_environment, Path(__file__).resolve().parents[1])
    for key in GDEXT_ENV_KEYS:
        environment.pop(key, None)
    environment["GDRUST_GODOT_API_JSON"] = str(api_path)

    interface_lock = lock.get("files", {}).get("gdextension_interface_json", {})
    mode = interface_lock.get("mode")
    if mode is None and interface_lock.get("available") is False:
        mode = "bundled"
    if mode not in {"bundled", "explicit"}:
        raise IdentityError("files.gdextension_interface_json.mode: expected bundled or explicit")
    report["interface"] = {"mode": mode}
    if mode == "explicit" and interface_json is None:
        raise IdentityError("explicit interface mode requires --interface-json")
    if interface_json is not None:
        if mode != "explicit":
            raise IdentityError("interface override is forbidden by bundled-only lock")
        expected_hash = interface_lock.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash):
            raise IdentityError("files.gdextension_interface_json.sha256: required SHA-256 is missing")
        interface_json = interface_json.resolve()
        if not interface_json.is_file():
            raise IdentityError("GDRUST_GODOT_INTERFACE_JSON: file does not exist")
        actual_hash = sha256_file(interface_json)
        if actual_hash != expected_hash.upper():
            raise IdentityError("files.gdextension_interface_json.sha256: lock is stale")
        try:
            json.loads(interface_json.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise IdentityError("interface: invalid JSON") from error
        report["interface"]["sha256"] = actual_hash
        report["companion_sha256"]["gdextension_interface_json"] = actual_hash
        environment["GDRUST_GODOT_INTERFACE_JSON"] = str(interface_json)

    return environment, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--api", type=Path)
    parser.add_argument("--interface-json", type=Path)
    parser.add_argument("--print-env", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    try:
        lock_path = args.lock.resolve()
        lock = load_lock(lock_path)
        api_path = args.api
        if api_path is None:
            api_value = lock.get("files", {}).get("extension_api", {}).get("path")
            if not isinstance(api_value, str):
                raise IdentityError("files.extension_api.path: required field is missing")
            api_path = resolve_locked_path(lock_path, api_value)
        environment, report = prepare_environment(
            lock_path, api_path, args.interface_json, os.environ
        )
    except IdentityError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    gdext_environment = {
        key: environment[key] for key in GDEXT_ENV_KEYS if key in environment
    }
    if args.print_env:
        print(
            json.dumps(
                {"environment": gdext_environment, "identity": report},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required unless --print-env is used")
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
