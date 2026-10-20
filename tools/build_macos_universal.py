# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build and audit universal macOS GDExtension libraries in a pinned container."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import IdentityError, inspect_api, load_lock  # noqa: E402
from tools.verify_binary_deps import BinaryAuditError, audit_binary  # noqa: E402


DEFAULT_IMAGE = (
    "ghcr.io/rust-cross/cargo-zigbuild@"
    "sha256:b8364c2c60cdcc9b95c402d17654bff517410926a35678bd89dd924b8158d6ae"
)
PROFILES = ("debug", "release")
OUTPUT_NAMES = {
    "debug": "libredot_rust_smoke.macos.template_debug.universal.dylib",
    "release": "libredot_rust_smoke.macos.template_release.universal.dylib",
}


class MacBuildError(RuntimeError):
    """Raised when a pinned macOS cross-build cannot be completed."""


def docker_command(
    repo_root: Path,
    api_path: Path,
    output_dir: Path,
    profile: str,
    image: str = DEFAULT_IMAGE,
) -> list[str]:
    if profile not in PROFILES:
        raise MacBuildError(f"unsupported profile: {profile}")
    release = " --release" if profile == "release" else ""
    script = (
        "rustup toolchain install 1.94.0 --profile minimal && "
        "rustup target add --toolchain 1.94.0 "
        "aarch64-apple-darwin x86_64-apple-darwin && "
        "GDRUST_GODOT_API_JSON=/api/extension_api.json "
        "CARGO_HOME=/output/cargo-home CARGO_TARGET_DIR=/output/target "
        "cargo +1.94.0 zigbuild --locked --workspace "
        f"--target universal2-apple-darwin{release}"
    )
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo_root.resolve()}:/workspace:ro",
        "-v",
        f"{api_path.resolve().parent}:/api:ro",
        "-v",
        f"{output_dir.resolve()}:/output",
        "-w",
        "/workspace",
        image,
        "sh",
        "-c",
        script,
    ]


def build_macos_universal(
    repo_root: Path,
    api_path: Path,
    output_dir: Path,
    lock_path: Path,
    *,
    profiles: tuple[str, ...] = PROFILES,
    image: str = DEFAULT_IMAGE,
    timeout: int = 1800,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    api_path = api_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    inspect_api(api_path, load_lock(lock_path.resolve()))
    binaries: dict[str, Any] = {}
    for profile in profiles:
        command = docker_command(repo_root, api_path, output_dir, profile, image)
        try:
            completed = subprocess.run(command, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise MacBuildError(f"{profile} build exceeded {timeout} seconds") from error
        if completed.returncode != 0:
            raise MacBuildError(
                f"{profile} cargo-zigbuild exited {completed.returncode}"
            )
        source = (
            output_dir
            / "target"
            / "universal2-apple-darwin"
            / profile
            / "libredot_rust_smoke.dylib"
        )
        if not source.is_file():
            raise MacBuildError(f"{profile} universal library is missing: {source}")
        destination = output_dir / "bin" / OUTPUT_NAMES[profile]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        binaries[profile] = audit_binary(
            destination,
            expected_architectures={"arm64", "x86_64"},
            expected_target="macos-universal",
        )
        if binaries[profile]["status"] != "PASS":
            raise MacBuildError(
                f"{profile} binary audit failed: {binaries[profile]['failures']}"
            )

    report = {
        "schema_version": 1,
        "status": "PASS",
        "image": image,
        "rust": "1.94.0",
        "target": "universal2-apple-darwin",
        "profiles": list(profiles),
        "command": [
            "docker",
            "run",
            "--rm",
            "<MOUNTS>",
            image,
            "cargo +1.94.0 zigbuild --locked --workspace --target universal2-apple-darwin",
        ],
        "binaries": binaries,
    }
    (output_dir / "build-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--api", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--profile", action="append", choices=PROFILES)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        profiles = tuple(args.profile) if args.profile else PROFILES
        report = build_macos_universal(
            args.repo_root,
            args.api,
            args.output_dir,
            args.lock,
            profiles=profiles,
            image=args.image,
            timeout=args.timeout,
        )
    except (BinaryAuditError, IdentityError, MacBuildError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
