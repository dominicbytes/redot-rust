# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fetch a checksum-locked official editor for an engine or binding profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import urllib.request
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import DEFAULT_LOCK, IdentityError, engine_profile, host_target, load_lock, sha256_file
from tools.scan_redot_log import EvidenceSanitizer


def fetch_engine(lock: dict, selected: str | None, target: str, output: Path) -> Path:
    name, identity = engine_profile(lock, selected)
    asset = identity["assets"][target]
    root = output.resolve() / name / target
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "editor.zip"
    if not archive.is_file() or sha256_file(archive) != asset["sha256"].upper():
        archive.unlink(missing_ok=True)
        with urllib.request.urlopen(asset["url"], timeout=120) as response, archive.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
    if sha256_file(archive) != asset["sha256"].upper():
        raise IdentityError("official release archive SHA-256 mismatch")
    destination = root / "editor"
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename or ":" in member.filename:
                raise IdentityError("unsafe editor archive member")
        if sys.platform == "darwin" and target == "macos-universal":
            subprocess.run(["ditto", "-x", "-k", str(archive), str(destination)], check=True, timeout=120)
        else:
            package.extractall(destination)
    for member, digest in asset["binaries"].items():
        binary = destination / member
        if sha256_file(binary) != digest.upper():
            raise IdentityError("extracted editor binary SHA-256 mismatch")
        binary.chmod(binary.stat().st_mode | 0o111)
    binary = destination / asset["executable"]
    if not binary.is_file():
        raise IdentityError("locked editor executable is absent from archive")
    return binary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--engine-profile")
    group.add_argument("--binding", action="store_true")
    parser.add_argument("--target")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--github-env", type=Path)
    args = parser.parse_args(argv)
    try:
        lock = load_lock(args.lock)
        selected = lock["binding_abi"]["engine_profile"] if args.binding else args.engine_profile
        name, _ = engine_profile(lock, selected)
        binary = fetch_engine(lock, selected, args.target or host_target(), args.output_dir)
        if args.github_env:
            key = "BINDING_REDOT_BIN" if args.binding else "REDOT_BIN"
            with args.github_env.open("a", encoding="utf-8") as stream:
                stream.write(f"{key}={binary}\n")
        print(json.dumps({"status": "PASS", "profile": name, "executable": binary.name}))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
