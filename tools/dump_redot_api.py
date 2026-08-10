# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate Redot GDExtension artifacts with the actual editor binary."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import (  # noqa: E402
    IdentityError,
    inspect_api,
    load_lock,
    sha256_file,
)


class DumpError(RuntimeError):
    """Raised when the editor cannot produce a complete validated snapshot."""


@dataclass(frozen=True)
class ResolvedEngine:
    editor: Path
    launcher: Path | None


def resolve_dump_editor(configured_path: Path) -> ResolvedEngine:
    configured = configured_path.expanduser().resolve()
    if not configured.is_file():
        raise DumpError(f"configured Redot binary does not exist: {configured}")
    if configured.name.lower().endswith(".console.exe"):
        sibling_name = configured.name[: -len(".console.exe")] + ".exe"
        sibling = configured.with_name(sibling_name)
        if not sibling.is_file():
            raise DumpError(
                "console launcher cannot run dump commands and its sibling editor "
                f"does not exist: {sibling}"
            )
        return ResolvedEngine(editor=sibling.resolve(), launcher=configured)
    return ResolvedEngine(editor=configured, launcher=None)


def _run(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DumpError(f"command failed to start or finish: {command}: {error}") from error
    if result.returncode != 0:
        raise DumpError(
            f"command exited {result.returncode}: {command}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def dump_snapshot(
    configured_bin: Path,
    output_dir: Path,
    lock_path: Path,
    timeout: int,
) -> dict:
    resolved = resolve_dump_editor(configured_bin)
    lock = load_lock(lock_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    version_command = resolved.launcher or resolved.editor
    version = _run([str(version_command), "--version"], output_dir, timeout)
    help_result = _run([str(version_command), "--help"], output_dir, timeout)
    if "--dump-extension-api" not in help_result.stdout:
        raise DumpError(
            f"actual editor does not advertise --dump-extension-api: {resolved.editor}"
        )

    with tempfile.TemporaryDirectory(prefix="redot-api-", dir=output_dir) as stage_name:
        stage = Path(stage_name)
        commands = [
            [str(resolved.editor), "--headless", "--dump-extension-api"],
            [str(resolved.editor), "--headless", "--dump-gdextension-interface"],
        ]
        command_results = [_run(command, stage, timeout) for command in commands]
        api = stage / "extension_api.json"
        interface = stage / "gdextension_interface.h"
        if not api.is_file() or not interface.is_file():
            raise DumpError(
                "Redot returned success without both extension_api.json and "
                "gdextension_interface.h"
            )

        try:
            report = inspect_api(
                api,
                lock,
                interface_header_path=interface,
                editor_path=resolved.editor,
                launcher_path=resolved.launcher,
            )
        except IdentityError as error:
            raise DumpError(f"generated snapshot does not match lock: {error}") from error

        for source in (api, interface):
            os.replace(source, output_dir / source.name)

    snapshot = {
        "status": "PASS",
        "configured_binary": str(Path(configured_bin).resolve()),
        "editor": str(resolved.editor),
        "launcher": str(resolved.launcher) if resolved.launcher else None,
        "version_stdout": version.stdout.strip(),
        "identity": report,
        "commands": [
            {
                "argv": commands[index],
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for index, result in enumerate(command_results)
        ],
    }
    (output_dir / "snapshot.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sums = [
        f"{sha256_file(output_dir / name)}  {name}"
        for name in ("extension_api.json", "gdextension_interface.h")
    ]
    (output_dir / "SHA256SUMS.txt").write_text(
        "\n".join(sums) + "\n", encoding="utf-8"
    )
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redot-bin", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        snapshot = dump_snapshot(
            args.redot_bin, args.output_dir, args.lock.resolve(), args.timeout
        )
    except (DumpError, IdentityError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(snapshot["identity"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
