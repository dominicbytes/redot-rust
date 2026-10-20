# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Scan Redot output for release-blocking diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


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


# Exclude URI and placeholder suffixes while recognizing drive, UNC and POSIX roots.
ABSOLUTE_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|]*"
    r"|(?<![\w:/<>])/(?![/\s])[^\r\n\"'<>|]*"
)


class EvidenceSanitizer:
    """Remove execution-only roots before any evidence or diagnostic is persisted."""

    def __init__(self, roots: dict[str | Path, str] | None = None) -> None:
        known = {
            Path.home(): "<HOME>", Path(tempfile.gettempdir()): "<TEMP_ROOT>",
            Path(__file__).resolve().parents[1]: "<REPO_ROOT>",
        }
        for key, placeholder in (
            ("CARGO_TARGET_DIR", "<BUILD_ROOT>"), ("CARGO_HOME", "<CACHE_ROOT>"),
            ("RUSTUP_HOME", "<TOOLCHAIN_ROOT>"), ("APPDATA", "<CACHE_ROOT>"),
            ("LOCALAPPDATA", "<CACHE_ROOT>"), ("TEMP", "<TEMP_ROOT>"), ("TMP", "<TEMP_ROOT>"),
        ):
            if os.environ.get(key):
                known[Path(os.environ[key])] = placeholder
        known.update(roots or {})
        self.roots = sorted(
            ((str(root).replace("\\", "/").rstrip("/"), value) for root, value in known.items()),
            key=lambda pair: len(pair[0]), reverse=True,
        )

    def text(self, value: str) -> str:
        for root, placeholder in self.roots:
            pattern = re.escape(root).replace("/", r"[\\/]")
            value = re.sub(pattern, lambda _match: placeholder, value, flags=re.I)
        return ABSOLUTE_PATH.sub("<ABSOLUTE_PATH>", value)

    def data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(key)): self.data(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.data(item) for item in value]
        return value

    def write_text(self, path: Path, value: str) -> None:
        path.write_text(self.text(value), encoding="utf-8")

    def write_json(self, path: Path, value: Any) -> Any:
        value = self.data(value)
        self.write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")
        return value

    def finish(
        self, directory: Path, filename: str, report: dict[str, Any],
    ) -> dict[str, Any]:
        report = self.data(report)
        # Result JSON is engine-written, so it must pass the same sanitizer too.
        for path in directory.rglob("*.json"):
            self.write_text(path, path.read_text(encoding="utf-8", errors="replace"))
        self.write_json(directory / filename, report)
        if scan_evidence(directory):
            report["status"] = "FAIL"
            report.setdefault("failures", []).append("absolute path remains in evidence directory")
            # Scrub textual evidence even on an always-upload failure path.
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix in (".log", ".json", ".md", ".txt"):
                    self.write_text(path, path.read_text(encoding="utf-8", errors="replace"))
            self.write_json(directory / filename, report)
        return report


def scan_evidence(directory: Path) -> list[str]:
    """Return neutral failures, never the offending paths or diagnostic text."""
    failures = []
    for path in directory.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            if any(ABSOLUTE_PATH.search(payload.decode(encoding, errors="replace"))
                   for encoding in ("utf-8", "utf-16-le", "utf-16-be")):
                failures.append("absolute path in evidence file")
    return failures


def scan_text(
    text: str, allowlist: list[str] | None = None,
    *, sanitizer: EvidenceSanitizer | None = None,
) -> list[dict[str, object]]:
    sanitizer = sanitizer or EvidenceSanitizer()
    allowlist = allowlist or []
    findings: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(entry in line for entry in allowlist):
            continue
        for severity, pattern in RULES:
            if pattern.search(line):
                findings.append(
                    {"severity": severity, "line_number": number, "line": sanitizer.text(line)}
                )
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--allow", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        text = (
            args.path.read_text(encoding="utf-8", errors="replace")
            if args.path
            else sys.stdin.read()
        )
    except OSError as error:
        print(EvidenceSanitizer().text(f"FAIL: {error}"), file=sys.stderr)
        return 2
    findings = scan_text(text, args.allow)
    print(json.dumps(findings, indent=2))
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
