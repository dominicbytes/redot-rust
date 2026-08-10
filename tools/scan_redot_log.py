# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Scan Redot output for release-blocking diagnostics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


RULES = (
    ("crash", re.compile(r"\b(crash(?:ed)?|signal 11|segmentation fault|panic)\b", re.I)),
    (
        "error",
        re.compile(
            r"\bERROR\b|failed to load|cannot get class|missing (?:symbol|library)|"
            r"could not load|parse error",
            re.I,
        ),
    ),
    ("warning", re.compile(r"\bWARNING\b|\bWARN(?:ING)?\b", re.I)),
)


def scan_text(text: str, allowlist: list[str] | None = None) -> list[dict[str, object]]:
    allowlist = allowlist or []
    findings: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(entry in line for entry in allowlist):
            continue
        for severity, pattern in RULES:
            if pattern.search(line):
                findings.append(
                    {"severity": severity, "line_number": number, "line": line}
                )
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--allow", action="append", default=[])
    args = parser.parse_args(argv)
    text = (
        args.path.read_text(encoding="utf-8", errors="replace")
        if args.path
        else sys.stdin.read()
    )
    findings = scan_text(text, args.allow)
    print(json.dumps(findings, indent=2))
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
