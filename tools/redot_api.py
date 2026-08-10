# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fail-closed Redot product and Godot API identity validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class IdentityError(ValueError):
    """Raised when an artifact does not match its declared identity lock."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityError(f"lock: cannot read {path}: {error}") from error
    if value.get("schema_version") != 1:
        raise IdentityError(
            f"schema_version: expected 1, found {value.get('schema_version')!r}"
        )
    return value


def resolve_locked_path(lock_path: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = lock_path.resolve().parent / path
    return path.resolve()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IdentityError(f"{field}: required object is missing")
    return value


def _expect_exact(
    actual: dict[str, Any], expected: dict[str, Any], field: str, keys: tuple[str, ...]
) -> None:
    for key in keys:
        if key not in actual:
            raise IdentityError(f"{field}.{key}: required field is missing")
        if key in expected and actual[key] != expected[key]:
            raise IdentityError(
                f"{field}.{key}: lock expects {expected[key]}, found {actual[key]}"
            )


def _verify_hash(path: Path, expected: str, field: str) -> str:
    if not path.is_file():
        raise IdentityError(f"{field}: file does not exist: {path}")
    actual = sha256_file(path)
    if actual != expected.upper():
        raise IdentityError(
            f"{field}: lock is stale; expected {expected.upper()}, found {actual}"
        )
    return actual


def inspect_api(
    api_path: Path,
    lock: dict[str, Any],
    *,
    interface_header_path: Path | None = None,
    editor_path: Path | None = None,
    launcher_path: Path | None = None,
) -> dict[str, Any]:
    """Validate one API JSON and optional companion artifacts against a lock."""

    files = _mapping(lock.get("files"), "files")
    api_lock = _mapping(files.get("extension_api"), "files.extension_api")
    expected_hash = api_lock.get("sha256")
    if not isinstance(expected_hash, str):
        raise IdentityError("files.extension_api.sha256: required field is missing")
    api_hash = _verify_hash(
        Path(api_path), expected_hash, "files.extension_api.sha256"
    )

    try:
        document = json.loads(Path(api_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityError(f"extension_api: invalid JSON: {error}") from error

    if not isinstance(document.get("header"), dict):
        raise IdentityError("header: required standard Godot API header is missing")
    header = document["header"]
    redot_header = _mapping(document.get("redot_header"), "redot_header")
    expected_api = _mapping(lock.get("godot_api"), "godot_api")
    expected_redot = _mapping(lock.get("redot"), "redot")
    target = _mapping(lock.get("target"), "target")

    major = header.get("version_major")
    if major != 4:
        raise IdentityError(f"header.version_major: expected 4, found {major}")

    minor = header.get("version_minor")
    minimum = expected_api.get("supported_minor_min")
    maximum = expected_api.get("supported_minor_max")
    if not isinstance(minor, int):
        raise IdentityError("header.version_minor: required integer is missing")
    if not isinstance(minimum, int) or not isinstance(maximum, int):
        raise IdentityError("godot_api: supported minor range is missing")
    if not minimum <= minor <= maximum:
        raise IdentityError(
            f"header.version_minor: supported range is {minimum}..{maximum}, found {minor}"
        )

    _expect_exact(
        header,
        expected_api,
        "header",
        (
            "version_major",
            "version_minor",
            "version_patch",
            "version_status",
            "version_build",
        ),
    )
    _expect_exact(
        redot_header,
        expected_redot,
        "redot_header",
        (
            "version_major",
            "version_minor",
            "version_patch",
            "version_status",
            "version_build",
        ),
    )

    target_precision = target.get("precision")
    for field, value in (
        ("header.precision", header.get("precision")),
        ("redot_header.precision", redot_header.get("precision")),
    ):
        if value != target_precision:
            raise IdentityError(
                f"{field}: target expects {target_precision}, found {value}"
            )

    companion_hashes: dict[str, str] = {}
    optional = (
        (
            "gdextension_interface_header",
            interface_header_path,
            "files.gdextension_interface_header.sha256",
        ),
    )
    for lock_key, supplied_path, field in optional:
        if supplied_path is None:
            continue
        entry = _mapping(files.get(lock_key), f"files.{lock_key}")
        companion_hashes[lock_key] = _verify_hash(
            supplied_path, str(entry.get("sha256", "")), field
        )

    binaries = lock.get("binaries", {})
    for lock_key, supplied_path in (
        ("editor", editor_path),
        ("console_launcher", launcher_path),
    ):
        if supplied_path is None:
            continue
        entry = _mapping(binaries.get(lock_key), f"binaries.{lock_key}")
        companion_hashes[lock_key] = _verify_hash(
            supplied_path,
            str(entry.get("sha256", "")),
            f"binaries.{lock_key}.sha256",
        )

    api_version = (
        f"{header['version_major']}.{header['version_minor']}.{header['version_patch']}"
    )
    redot_version = (
        f"{redot_header['version_major']}.{redot_header['version_minor']}."
        f"{redot_header['version_patch']}"
    )
    return {
        "status": "PASS",
        "redot_product": redot_version,
        "redot_full_name": redot_header.get("version_full_name"),
        "godot_api": api_version,
        "godot_api_full_name": header.get("version_full_name"),
        "precision": target_precision,
        "api_sha256": api_hash,
        "companion_sha256": companion_hashes,
        "expected_cfgs": [
            f'since_api="4.{minor}"',
            f'before_api="4.{minor + 1}"',
        ],
    }
