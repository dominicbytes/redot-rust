# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Create a deterministic, target-specific Redot add-on archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dependency_report import DependencyReportError, third_party_bundle  # noqa: E402
from tools.redot_api import binding_abi, binding_identity, inspect_api, load_lock, resolve_locked_path, sha256_file  # noqa: E402
from tools.verify_binary_deps import (  # noqa: E402
    BinaryAuditError,
    audit_binary,
)


VERSION = "0.2.0"
ADDON_ROOT = PurePosixPath("addons/redot-rust-compat")
MANIFEST_NAME = str(ADDON_ROOT / "package-manifest.json")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

TARGETS = {
    "windows-x86_64": {
        "architectures": {"x86_64"},
        "cargo_targets": ("x86_64-pc-windows-msvc",),
        "debug_name": "redot_rust_smoke.windows.template_debug.x86_64.dll",
        "release_name": "redot_rust_smoke.windows.template_release.x86_64.dll",
        "debug_key": "windows.debug.x86_64",
        "release_key": "windows.release.x86_64",
    },
    "linux-x86_64": {
        "architectures": {"x86_64"},
        "cargo_targets": ("x86_64-unknown-linux-gnu",),
        "debug_name": "libredot_rust_smoke.linux.template_debug.x86_64.so",
        "release_name": "libredot_rust_smoke.linux.template_release.x86_64.so",
        "debug_key": "linux.debug.x86_64",
        "release_key": "linux.release.x86_64",
    },
    "macos-universal": {
        "architectures": {"arm64", "x86_64"},
        "cargo_targets": ("aarch64-apple-darwin", "x86_64-apple-darwin"),
        "debug_name": "libredot_rust_smoke.macos.template_debug.universal.dylib",
        "release_name": "libredot_rust_smoke.macos.template_release.universal.dylib",
        "debug_key": "macos.debug",
        "release_key": "macos.release",
    },
}

DOCUMENTS = {
    "LICENSE": ADDON_ROOT / "LICENSE",
    "NOTICE.md": ADDON_ROOT / "NOTICE.md",
    "docs/package-readme.md": ADDON_ROOT / "README.md",
    "UPSTREAM_LOCK.md": ADDON_ROOT / "UPSTREAM_LOCK.md",
    "redot-api.lock.json": ADDON_ROOT / "redot-api.lock.json",
    "docs/compatibility.md": ADDON_ROOT / "docs/compatibility.md",
    "docs/limitations.md": ADDON_ROOT / "docs/limitations.md",
    "docs/lock-schema.md": ADDON_ROOT / "docs/lock-schema.md",
}

SOURCE_REPOSITORY = "https://github.com/dominicbytes/redot-rust"
POSIX_ROOTS = (
    r"/(?:home|Users|tmp|private/tmp|var/folders|root|workspace|builds|__w|runner)/"
    r"|/github/workspace(?:/|$)|/opt/(?:hostedtoolcache|actions-runner)/"
    r"|/[a-z]/(?:Users|home|workspace)/"
)
PRIVATE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+|" + POSIX_ROOTS +
    r"|\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_$.-]+)", re.I
)
# Native libraries contain short printable fragments by chance, such as a
# bare `A:\` between NUL bytes in a large debug library. A leaked path always
# names something below its root, so require a path character after a drive
# root and a further separator after a UNC share. UNC shares only exist on
# Windows build hosts, and the signed tables of Mach-O libraries can spell
# one by chance, so only PE libraries are checked for them.
BINARY_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]+[^\x00-\x1f\\/:*?\"<>|]|" + POSIX_ROOTS + ")", re.I
)
UNC_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_$.-]+\\")
# A Unicode POSIX root needs a string/text boundary and a second separator,
# using ASCII separators because numeric tables can decode as Unicode spaces.
# It is only applied to UTF-8 text: UTF-16 decodings of instructions and
# numeric tables routinely produce non-ASCII runs between slashes, while
# real UTF-16 paths in native libraries are Windows drive paths.
UNICODE_POSIX_ROOT_PATTERN = re.compile(r"(?<![^\x09-\x0d \"'=\x00])/[^\x00-\x7f/\\\s][^/\\\x00-\x1f]*/")


class PackageError(RuntimeError):
    """Raised when a release archive would violate its frozen contract."""


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _gdextension(target: dict[str, Any], lock: dict[str, Any]) -> bytes:
    debug_path = ADDON_ROOT / "bin" / target["debug_name"]
    release_path = ADDON_ROOT / "bin" / target["release_name"]
    api = binding_abi(lock)["godot_api"]
    minimum = f'{api["version_major"]}.{api["version_minor"]}'
    text = (
        '[configuration]\n\n'
        'entry_symbol = "gdext_rust_init"\n'
        f'compatibility_minimum = "{minimum}"\n'
        'reloadable = false\n\n'
        '[libraries]\n\n'
        f'{target["debug_key"]} = "res://{debug_path}"\n'
        f'{target["release_key"]} = "res://{release_path}"\n'
    )
    return text.encode("utf-8")


def _expand_debug(data: bytes, size: int, kind: int) -> bytes:
    # Bound expansion even when a shipping library has a corrupt size header.
    if size > 256 * 1024 * 1024:
        raise PackageError("compressed debug section exceeds scan limit")
    if kind == 1:
        decoder = zlib.decompressobj()
        try:
            result = decoder.decompress(data, size + 1)
        except zlib.error as error:
            raise PackageError("invalid compressed debug section") from error
        complete = decoder.eof and not decoder.unused_data
    elif kind == 2:
        try:
            from compression import zstd
        except ImportError as error:
            raise PackageError("zstd debug sections require stdlib support") from error
        try:
            decoder = zstd.ZstdDecompressor()
            result = decoder.decompress(data, max_length=size + 1)
        except zstd.ZstdError as error:
            raise PackageError("invalid compressed debug section") from error
        complete = decoder.eof and not decoder.unused_data
    else:
        raise PackageError("unsupported compressed debug section")
    if not complete or len(result) != size:
        raise PackageError("compressed debug section size or stream mismatch")
    return result


def _compressed_debug_payloads(data: bytes) -> list[bytes]:
    """Expand ELF debug data; reject opaque PE and Mach-O debug compression."""
    payloads = []

    def blob(offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(data):
            raise PackageError("truncated native debug data")
        return data[offset:offset + size]

    def fields(fmt: str, offset: int) -> tuple:
        return struct.unpack(fmt, blob(offset, struct.calcsize(fmt)))

    if data.startswith(b"\x7fELF"):
        if blob(4, 2) != b"\x02\x01":
            raise PackageError("unsupported ELF debug section layout")
        table = fields("<Q", 40)[0]
        stride, count, names_index = fields("<HHH", 58)
        if stride < 64 or count == 0 or names_index == 0xffff:
            raise PackageError("unsupported ELF debug section table")
        sections = [fields("<IIQQQQIIQQ", table + i * stride) for i in range(count)]
        if names_index >= count:
            raise PackageError("invalid ELF section name table")
        names_section = sections[names_index]
        names = blob(names_section[4], names_section[5])
        for section in sections:
            name_offset, section_type, flags = section[:3]
            if name_offset >= len(names) and name_offset != 0:
                raise PackageError("invalid ELF section name")
            name = names[name_offset:].split(b"\0", 1)[0]
            if flags & 0x800:  # SHF_COMPRESSED; inspect regardless of section name.
                if section_type == 8 or section[5] < 24:
                    raise PackageError("invalid ELF compressed section")
                packed = blob(section[4], section[5])
                kind, reserved, size, _alignment = struct.unpack_from("<IIQQ", packed)
                if reserved:
                    raise PackageError("invalid ELF compression header")
                payloads.append(_expand_debug(packed[24:], size, kind))
            elif name == b".gnu_debugdata":
                raise PackageError("unsupported ELF compressed mini-debug data")
            elif name.startswith(b".zdebug"):
                packed = blob(section[4], section[5])
                if len(packed) < 12 or packed[:4] != b"ZLIB":
                    raise PackageError("unsupported legacy compressed debug section")
                payloads.append(_expand_debug(packed[12:], int.from_bytes(packed[4:12], "big"), 1))
    elif data.startswith(b"MZ"):
        pe = fields("<I", 0x3c)[0]
        count = fields("<H", pe + 6)[0]
        optional_size = fields("<H", pe + 20)[0]
        optional = pe + 24
        magic = fields("<H", optional)[0]
        directory = {0x10b: 96, 0x20b: 112}.get(magic)
        if directory is None:
            raise PackageError("unsupported PE debug directory layout")
        sections = []
        for i in range(count):
            offset = optional + optional_size + i * 40
            name = blob(offset, 8).split(b"\0", 1)[0]
            if name.startswith(b"/"):
                # COFF long section names live after the symbol table.
                symbols, symbol_count = fields("<II", pe + 12)
                try:
                    name_offset = int(name[1:])
                except ValueError as error:
                    raise PackageError("invalid COFF section name") from error
                strings = symbols + symbol_count * 18
                string_size = fields("<I", strings)[0]
                if not 4 <= name_offset < string_size:
                    raise PackageError("invalid COFF section name offset")
                name = blob(strings + name_offset, string_size - name_offset).split(b"\0", 1)[0]
            if name.startswith(b".zdebug"):
                raise PackageError("unsupported PE compressed debug section")
            sections.append(fields("<IIII", offset + 8))
        if optional_size >= directory + 7 * 8:
            rva, size = fields("<II", optional + directory + 6 * 8)
            if size:
                if size % 28:
                    raise PackageError("invalid PE debug directory size")
                for _virtual_size, address, raw_size, raw in sections:
                    if address <= rva and rva + size <= address + raw_size:
                        debug = blob(raw + rva - address, size)
                        break
                else:
                    raise PackageError("PE debug directory has no file-backed data")
                for offset in range(0, size, 28):
                    if struct.unpack_from("<I", debug, offset + 12)[0] == 17:
                        raise PackageError("unsupported PE embedded compressed PDB")
    elif data[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        wide = data[3] == 0xbf
        stride = 32 if wide else 20
        for i in range(fields(">I", 4)[0]):
            offset, size = fields(">QQ" if wide else ">II", 8 + i * stride + 8)
            payloads.extend(_compressed_debug_payloads(blob(offset, size)))
    elif data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
        endian = "<" if data[0] == 0xcf else ">"
        count, size = fields(endian + "II", 16)
        cursor, end = 32, 32 + size
        blob(cursor, size)
        for _ in range(count):
            if cursor + 8 > end:
                raise PackageError("truncated Mach-O debug load command")
            command, size = fields(endian + "II", cursor)
            if size < 8 or cursor + size > end:
                raise PackageError("invalid Mach-O debug load command")
            if command == 0x19:  # LC_SEGMENT_64
                section_count = fields(endian + "I", cursor + 64)[0]
                if size < 72 + section_count * 80:
                    raise PackageError("truncated Mach-O debug section table")
                for i in range(section_count):
                    name = blob(cursor + 72 + i * 80, 16).split(b"\0", 1)[0]
                    if name.startswith(b"__zdebug"):
                        raise PackageError("unsupported Mach-O compressed debug section")
            cursor += size
    return payloads


def _validate_payload(name: str, data: bytes) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise PackageError(f"unsafe archive path: {name}")
    lowered = path.name.casefold()
    if lowered in {"extension_api.json", "gdextension_interface.h"}:
        raise PackageError(f"restricted generated artifact in package: {name}")
    if path.suffix.casefold() in {".exe", ".tpz", ".pdb", ".lib", ".exp"}:
        raise PackageError(f"restricted build/engine artifact in package: {name}")
    # Check every payload, including native libraries and extensionless license files.
    native_binary = path.suffix.casefold() in {".dll", ".so", ".dylib"}
    texts = []
    # Native-library strings decoded as UTF-8 and as UTF-16; see
    # BINARY_PATH_PATTERN and UNICODE_POSIX_ROOT_PATTERN.
    native_texts = []
    wide_texts = []
    payloads = [data]
    if native_binary:
        payloads.extend(_compressed_debug_payloads(data))
    for payload in payloads:
        decoded_strings = [(payload.decode("utf-8", errors="surrogateescape"), False)]
        for encoding in ("utf-16le", "utf-16be"):
            for offset in (0, 1):
                aligned = payload[offset:]
                decoded_strings.append((aligned[:len(aligned) // 2 * 2].decode(
                    encoding, errors="surrogatepass"
                ), True))
        for decoded, wide in decoded_strings:
            if not native_binary:
                texts.append(decoded)
                continue
            # Invalid code units and Unicode noncharacters delimit strings.
            # Keep the NUL-boundary rule for short instruction-like fragments.
            for match in re.finditer(r"[^\x00-\x1f\x7f-\x9f\ud800-\udfff\ufdd0-\ufdef\ufffe\uffff]{3,}", decoded):
                if len(match.group()) >= 6 or match.start() == 0 or decoded[match.start() - 1] == "\0":
                    (wide_texts if wide else native_texts).append(match.group())
    if path.suffix.casefold() == ".json":
        # JSON escaping must not hide a path from the publication gate.
        def strings(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, dict):
                return [text for pair in value.items() for item in pair for text in strings(item)]
            if isinstance(value, list):
                return [text for item in value for text in strings(item)]
            return []
        texts.extend(strings(json.loads(data)))
    checks = [
        (PRIVATE_PATH_PATTERN, texts),
        (BINARY_PATH_PATTERN, native_texts + wide_texts),
        (UNC_PATH_PATTERN, native_texts + wide_texts if path.suffix.casefold() == ".dll" else []),
        (UNICODE_POSIX_ROOT_PATTERN, texts + native_texts),
    ]
    for pattern, candidates in checks:
        for text in candidates:
            match = pattern.search(text)
            if match:
                # Do not copy the private value into diagnostics or uploaded reports;
                # name only the kind of root that matched.
                raise PackageError(f"private absolute path in {name} ({_root_kind(match.group())})")


def _root_kind(matched: str) -> str:
    if matched.startswith("\\\\"):
        return "UNC path"
    if re.match(r"[A-Za-z]:", matched):
        return "drive path"
    known = re.match(r"/(?:[a-z]/)?[A-Za-z_]+(?:/[A-Za-z_-]+)?/?", matched)
    if known and matched.isascii():
        return f"{known.group()} root"
    return "non-ASCII POSIX root"


def create_package(
    repo_root: Path,
    target_name: str,
    debug_binary: Path,
    release_binary: Path,
    output_dir: Path,
    *,
    version: str = VERSION,
    cargo: str = "cargo",
    lock_path: Path | None = None,
    api_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    if target_name not in TARGETS:
        raise PackageError(f"unsupported target: {target_name}")
    target = TARGETS[target_name]
    lock_path = lock_path.resolve() if lock_path else repo_root / "redot-api.lock.json"
    lock = load_lock(lock_path)
    revision_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
        text=True, timeout=30, check=False,
    )
    revision = revision_result.stdout.strip()
    if revision_result.returncode or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise PackageError("cannot determine corresponding source revision")
    source_locator = {"repository": SOURCE_REPOSITORY, "revision": revision}

    audits = {
        "debug": audit_binary(
            debug_binary,
            expected_architectures=target["architectures"],
            expected_target=target_name,
        ),
        "release": audit_binary(
            release_binary,
            expected_architectures=target["architectures"],
            expected_target=target_name,
        ),
    }
    failed = [profile for profile, audit in audits.items() if audit["status"] != "PASS"]
    if failed:
        raise PackageError(f"binary audit failed for profile(s): {', '.join(failed)}")

    api_path = api_path or resolve_locked_path(lock_path, binding_abi(lock)["files"]["extension_api"]["path"])
    inspect_api(api_path, lock)

    payload: dict[str, bytes] = {}
    for source_name, archive_name in DOCUMENTS.items():
        source = lock_path if source_name == "redot-api.lock.json" else repo_root / source_name
        if not source.is_file():
            raise PackageError(f"required package document is missing: {source_name}")
        payload[str(archive_name)] = source.read_bytes()
    for name, data in third_party_bundle(repo_root, target["cargo_targets"], cargo).items():
        payload[str(ADDON_ROOT / name)] = data
    payload[str(ADDON_ROOT / "SOURCE.json")] = (
        json.dumps(source_locator, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    payload[str(ADDON_ROOT / "redot_rust_smoke.gdextension")] = _gdextension(target, lock)
    payload[str(ADDON_ROOT / "bin" / target["debug_name"])] = debug_binary.read_bytes()
    payload[str(ADDON_ROOT / "bin" / target["release_name"])] = release_binary.read_bytes()

    for name, data in payload.items():
        _validate_payload(name, data)
    files = [
        {
            "path": name,
            "sha256": hashlib.sha256(payload[name]).hexdigest().upper(),
            "size": len(payload[name]),
        }
        for name in sorted(payload)
    ]
    manifest = {
        "schema_version": 2,
        "name": "redot-rust-compat",
        "version": version,
        "target": target_name,
        "source": source_locator,
        "binding_abi": binding_identity(lock),
        "certified_engines": lock["certified_engines"],
        "coverage": binding_abi(lock)["coverage"],
        "profiles": {
            profile: {
                "library": target[f"{profile}_name"],
                "sha256": audit["sha256"],
                "architectures": audit["architectures"],
                "dependencies": audit["dependencies"],
            }
            for profile, audit in audits.items()
        },
        "files": files,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    _validate_payload(MANIFEST_NAME, manifest_bytes)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_version = version.replace("+", "_")
    archive = output_dir / f"redot-rust-compat-{safe_version}-{target_name}.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as package:
        for name in sorted(payload):
            package.writestr(_zip_info(name), payload[name])
        package.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)

    archive_sha256 = sha256_file(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{archive_sha256}  {archive.name}\n", encoding="ascii")
    report = {
        "schema_version": 2,
        "status": "PASS",
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "archive_size": archive.stat().st_size,
        "checksum": checksum.name,
        "manifest": manifest,
    }
    report_path = output_dir / f"{archive.stem}.package-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--debug-binary", required=True, type=Path)
    parser.add_argument("--release-binary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--api", type=Path, help="binding snapshot to validate before packaging")
    args = parser.parse_args(argv)
    try:
        report = create_package(
            args.repo_root,
            args.target,
            args.debug_binary,
            args.release_binary,
            args.output_dir,
            version=args.version,
            cargo=args.cargo,
            lock_path=args.lock, api_path=args.api,
        )
    except (BinaryAuditError, DependencyReportError, PackageError, OSError, ValueError,
            subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
