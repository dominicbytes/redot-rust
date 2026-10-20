# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fail-closed Redot product and Godot API identity validation."""

from __future__ import annotations

import copy
import hashlib
import json
import platform
import re
import subprocess
import sys
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


DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "redot-api.lock.json"


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise IdentityError("lock: duplicate object key")
        result[key] = value
    return result


def validate_lock(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 2:
        raise IdentityError("schema_version: expected 2; migrate legacy v1 explicitly")
    legacy = {"profile", "redot", "godot_api", "target", "files", "binaries", "gdext", "rust"}
    if legacy.intersection(value):
        raise IdentityError("lock: mixed v1/v2 identity shape")
    binding = _mapping(value.get("binding_abi"), "binding_abi")
    for name in ("redot", "godot_api", "files", "target", "gdext", "rust", "coverage"):
        _mapping(binding.get(name), "binding_abi." + name)
    if not isinstance(binding.get("profile"), str) or not binding["profile"]:
        raise IdentityError("binding_abi.profile: required identity is missing")
    if binding.get("interface_mode") not in ("pinned-gdext-bundled", "explicit"):
        raise IdentityError("binding_abi.interface_mode: unsupported mode")
    mode = binding["files"].get("gdextension_interface_json", {}).get("mode")
    expected_mode = "bundled" if binding["interface_mode"] == "pinned-gdext-bundled" else "explicit"
    if mode != expected_mode:
        raise IdentityError("binding_abi interface mode is inconsistent")
    coverage = binding["coverage"]
    if (not coverage.get("unsupported_variant_types") or
            coverage.get("engine_only_classes") != "unsupported" or
            coverage.get("engine_only_enum_values") != "unsupported"):
        raise IdentityError("binding_abi.coverage: explicit restrictions are required")
    engines = _mapping(value.get("certified_engines"), "certified_engines")
    if not engines or value.get("default_engine_profile") not in engines or binding.get("engine_profile") not in engines:
        raise IdentityError("lock: default and binding engine profiles must exist")
    for name, identity, is_binding in [("binding_abi", binding, True), *[(key, item, False) for key, item in engines.items()]]:
        identity = _mapping(identity, name)
        redot = _mapping(identity.get("redot"), name + ".redot")
        _mapping(identity.get("godot_api"), name + ".godot_api")
        for field in ("version_string", "engine_commit", "precision"):
            if not isinstance(redot.get(field), str) or not redot[field]:
                raise IdentityError(name + ".redot." + field + ": required identity is missing")
        if not re.fullmatch(r"[a-f0-9]{40}", redot["engine_commit"]):
            raise IdentityError(name + ": exact engine commit is required")
        for section in ("redot", "godot_api"):
            header = identity[section]
            for part in ("major", "minor", "patch"):
                if type(header.get("version_" + part)) is not int:
                    raise IdentityError(name + ": complete numeric version identity is required")
            for part in ("status", "build", "full_name"):
                if not isinstance(header.get("version_" + part), str) or not header["version_" + part]:
                    raise IdentityError(name + ": complete version metadata is required")
            if header.get("precision") != redot["precision"]:
                raise IdentityError(name + ": mixed precision identity")
        if redot["precision"] not in ("single", "double"):
            raise IdentityError(name + ": invalid precision")
        files = _mapping(identity.get("files"), name + ".files")
        for key in ("extension_api", "gdextension_interface_header"):
            digest = files.get(key, {}).get("sha256", "")
            if not isinstance(digest, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", digest):
                raise IdentityError(name + ".files." + key + ": required SHA-256 is missing")
        if is_binding:
            continue
        if identity.get("binding_abi") != binding["profile"]:
            raise IdentityError(name + ": unknown binding ABI")
        assets = _mapping(identity.get("assets"), name + ".assets")
        if set(assets) != {"windows-x86_64", "linux-x86_64", "macos-universal"}:
            raise IdentityError(name + ": all three platform assets are required")
        for asset in assets.values():
            if asset.get("executable") not in asset.get("binaries", {}):
                raise IdentityError(name + ": executable must have a locked binary hash")
            if (not str(asset.get("url", "")).startswith("https://github.com/Redot-Engine/redot-engine/releases/download/")
                    or not re.fullmatch(r"[A-Fa-f0-9]{64}", str(asset.get("sha256", "")))
                    or not asset.get("executable") or not asset.get("binaries")):
                raise IdentityError(name + ": incomplete official archive identity")
            for member, digest in asset["binaries"].items():
                if (not member or Path(member).is_absolute() or ".." in Path(member).parts
                        or not re.fullmatch(r"[A-Fa-f0-9]{64}", str(digest))):
                    raise IdentityError(name + ": invalid binary identity")
    gdext = binding["gdext"]
    if (not re.fullmatch(r"[a-f0-9]{40}", str(gdext.get("revision", "")))
            or gdext.get("feature") != "api-custom-json" or not gdext.get("repository")
            or not gdext.get("crate_version") or not binding["rust"].get("cargo_version")):
        raise IdentityError("binding_abi: complete binding pin and toolchain are required")
    return value


def load_lock(path: Path = DEFAULT_LOCK) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object)
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityError("lock: cannot read valid JSON") from error
    return validate_lock(value)


def binding_abi(lock: dict[str, Any]) -> dict[str, Any]:
    return validate_lock(lock)["binding_abi"]


def engine_profile(lock: dict[str, Any], selected: str | None) -> tuple[str, dict[str, Any]]:
    validate_lock(lock)
    name = selected if selected is not None else lock["default_engine_profile"]
    if name not in lock["certified_engines"]:
        raise IdentityError("unknown engine profile")
    return name, lock["certified_engines"][name]


def host_target() -> str:
    if platform.machine().lower() not in ("amd64", "x86_64", "arm64", "aarch64"):
        raise IdentityError("unsupported engine host architecture")
    if sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"):
        return "windows-x86_64"
    if sys.platform.startswith("linux") and platform.machine().lower() in ("amd64", "x86_64"):
        return "linux-x86_64"
    if sys.platform == "darwin":
        return "macos-universal"
    raise IdentityError("unsupported native engine host")


def verify_engine(binary: Path, lock: dict[str, Any], selected: str | None = None,
                  *, target: str | None = None, timeout: int = 30,
                  version_command: list[str] | None = None) -> dict[str, Any]:
    name, identity = engine_profile(lock, selected)
    target = target or host_target()
    asset = identity["assets"].get(target)
    if asset is None:
        raise IdentityError("engine profile has no selected target")
    # A renamed local binary cannot bypass its locked content identity.
    digest = sha256_file(binary)
    if digest not in {value.upper() for value in asset["binaries"].values()}:
        raise IdentityError("runtime engine binary SHA-256 does not match selected profile")
    completed = subprocess.run(version_command or [str(binary), "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if completed.returncode or completed.stdout.strip() != identity["redot"]["version_string"]:
        raise IdentityError("runtime engine --version does not match selected profile")
    return {"profile": name, "target": target, "version_string": completed.stdout.strip(),
            "binary_sha256": digest, "identity": identity}


def binding_identity(lock: dict[str, Any]) -> dict[str, Any]:
    binding = copy.deepcopy(binding_abi(lock))
    for artifact in binding["files"].values():
        artifact.pop("path", None)
    return binding


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
    engine_profile_name: str | None = None,
    target_name: str | None = None,
) -> dict[str, Any]:
    """Validate one API JSON and optional companion artifacts against a lock."""

    full_lock = lock
    lock = (binding_abi(full_lock) if engine_profile_name is None
            else engine_profile(full_lock, engine_profile_name)[1])
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
    target = lock.get("target", {"precision": expected_redot["precision"]})

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

    for supplied_path in (editor_path, launcher_path):
        if supplied_path is not None:
            selected = engine_profile_name or full_lock["binding_abi"]["engine_profile"]
            runtime = verify_engine(supplied_path, full_lock, selected, target=target_name)
            companion_hashes[supplied_path.name] = runtime["binary_sha256"]

    api_version = (
        f"{header['version_major']}.{header['version_minor']}.{header['version_patch']}"
    )
    redot_version = (
        f"{redot_header['version_major']}.{redot_header['version_minor']}."
        f"{redot_header['version_patch']}"
    )
    return {
        "status": "PASS",
        "purpose": "engine-audit" if engine_profile_name is not None else "binding-input",
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
