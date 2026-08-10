# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Command-line API identity inspector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import (  # noqa: E402
    IdentityError,
    inspect_api,
    load_lock,
    resolve_locked_path,
)


def _locked_optional(lock_path: Path, lock: dict, *keys: str) -> Path | None:
    value = lock
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    if not isinstance(value, str):
        return None
    return resolve_locked_path(lock_path, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("redot-api.lock.json"))
    parser.add_argument("--api", type=Path)
    parser.add_argument("--interface-header", type=Path)
    parser.add_argument("--editor", type=Path)
    parser.add_argument("--launcher", type=Path)
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="validate only API identity/hash (for non-Windows host artifacts)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    try:
        lock_path = args.lock.resolve()
        lock = load_lock(lock_path)
        api = args.api or _locked_optional(lock_path, lock, "files", "extension_api", "path")
        if api is None:
            raise IdentityError("files.extension_api.path: required field is missing")
        if args.api_only and any(
            value is not None
            for value in (args.interface_header, args.editor, args.launcher)
        ):
            raise IdentityError("--api-only cannot be combined with companion artifacts")
        report = inspect_api(
            api.resolve(),
            lock,
            interface_header_path=(
                None if args.api_only else args.interface_header
                or _locked_optional(
                    lock_path,
                    lock,
                    "files",
                    "gdextension_interface_header",
                    "path",
                )
            ),
            editor_path=(
                None if args.api_only else args.editor
                or _locked_optional(lock_path, lock, "binaries", "editor", "path")
            ),
            launcher_path=(
                None if args.api_only else args.launcher
                or _locked_optional(
                    lock_path, lock, "binaries", "console_launcher", "path"
                )
            ),
        )
    except IdentityError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"PASS: Redot {report['redot_product']}; Godot API "
            f"{report['godot_api']}; {report['precision']} precision"
        )
        print(f"API SHA-256: {report['api_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
