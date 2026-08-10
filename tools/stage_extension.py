# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage locked debug and release GDExtension binaries into the fixture."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import sha256_file  # noqa: E402


class StageError(RuntimeError):
    """Raised when a complete binary set cannot be staged."""


TARGETS = {
    "windows-x86_64": {
        "platform": "windows",
        "architectures": ["x86_64"],
        "cargo_name": "redot_rust_smoke.dll",
        "debug_name": "redot_rust_smoke.windows.template_debug.x86_64.dll",
        "release_name": "redot_rust_smoke.windows.template_release.x86_64.dll",
    },
    "linux-x86_64": {
        "platform": "linux",
        "architectures": ["x86_64"],
        "cargo_name": "libredot_rust_smoke.so",
        "debug_name": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "release_name": "libredot_rust_smoke.linux.template_release.x86_64.so",
    },
    "macos-universal": {
        "platform": "macos",
        "architectures": ["arm64", "x86_64"],
        "cargo_name": None,
        "debug_name": "libredot_rust_smoke.macos.template_debug.universal.dylib",
        "release_name": "libredot_rust_smoke.macos.template_release.universal.dylib",
    },
}


def stage_target_binaries(
    debug_binary: Path,
    release_binary: Path,
    project_dir: Path,
    target_name: str,
) -> dict:
    if target_name not in TARGETS:
        raise StageError(f"unsupported target: {target_name}")
    project_dir = project_dir.resolve()
    sources = {
        "debug": debug_binary.resolve(),
        "release": release_binary.resolve(),
    }
    for profile, source in sources.items():
        if not source.is_file():
            raise StageError(f"{profile} binary does not exist: {source}")

    target = TARGETS[target_name]
    names = {profile: target[f"{profile}_name"] for profile in sources}
    bin_dir = project_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    profiles: dict[str, dict[str, object]] = {}
    for profile, source in sources.items():
        destination = bin_dir / names[profile]
        shutil.copy2(source, destination)
        profiles[profile] = {
            "file": destination.name,
            "sha256": sha256_file(destination),
            "size": destination.stat().st_size,
        }

    manifest = {
        "schema_version": 1,
        "target": target_name,
        "platform": target["platform"],
        "architectures": target["architectures"],
        "profiles": profiles,
    }
    (bin_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def stage_windows_binaries(target_dir: Path, project_dir: Path) -> dict:
    target_dir = target_dir.resolve()
    cargo_name = TARGETS["windows-x86_64"]["cargo_name"]
    return stage_target_binaries(
        target_dir / "debug" / cargo_name,
        target_dir / "release" / cargo_name,
        project_dir,
        "windows-x86_64",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default="windows-x86_64")
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--debug-binary", type=Path)
    parser.add_argument("--release-binary", type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.debug_binary is not None or args.release_binary is not None:
            if args.debug_binary is None or args.release_binary is None:
                raise StageError("--debug-binary and --release-binary must be provided together")
            manifest = stage_target_binaries(
                args.debug_binary,
                args.release_binary,
                args.project_dir,
                args.target,
            )
        elif args.target_dir is not None:
            cargo_name = TARGETS[args.target]["cargo_name"]
            if cargo_name is None:
                raise StageError(
                    f"{args.target} requires explicit --debug-binary and --release-binary"
                )
            manifest = stage_target_binaries(
                args.target_dir / "debug" / cargo_name,
                args.target_dir / "release" / cargo_name,
                args.project_dir,
                args.target,
            )
        else:
            raise StageError(
                "provide --target-dir or both --debug-binary and --release-binary"
            )
    except StageError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
