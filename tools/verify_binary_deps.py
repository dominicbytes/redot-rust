# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Audit GDExtension architecture, exports, and native dependencies."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.redot_api import sha256_file  # noqa: E402


class BinaryAuditError(RuntimeError):
    """Raised when a native binary is malformed or violates the contract."""


PE_ARCHITECTURES = {0x8664: "x86_64", 0xAA64: "arm64"}
ELF_ARCHITECTURES = {62: "x86_64", 183: "arm64"}
MACH_ARCHITECTURES = {0x01000007: "x86_64", 0x0100000C: "arm64"}


def _cstring(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        raise BinaryAuditError(f"string offset is outside the binary: {offset}")
    end = data.find(b"\0", offset)
    if end < 0:
        raise BinaryAuditError("unterminated string in binary table")
    return data[offset:end].decode("ascii", errors="replace")


def _pe_details(data: bytes) -> dict[str, Any]:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise BinaryAuditError("not a PE binary")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise BinaryAuditError("invalid PE signature")
    machine, section_count = struct.unpack_from("<HH", data, pe_offset + 4)
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    optional_offset = pe_offset + 24
    if optional_offset + optional_size > len(data):
        raise BinaryAuditError("truncated PE optional header")
    magic = struct.unpack_from("<H", data, optional_offset)[0]
    directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
    if directory_offset is None:
        raise BinaryAuditError(f"unsupported PE optional-header magic: 0x{magic:04X}")

    section_offset = optional_offset + optional_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        offset = section_offset + index * 40
        if offset + 40 > len(data):
            raise BinaryAuditError("truncated PE section table")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", data, offset + 8
        )
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))

    def rva_offset(rva: int, *, allow_zero: bool = False) -> int:
        if rva == 0 and allow_zero:
            return 0
        for virtual_address, span, raw_offset, raw_size in sections:
            if virtual_address <= rva < virtual_address + span:
                relative = rva - virtual_address
                if relative >= raw_size:
                    raise BinaryAuditError(f"RVA 0x{rva:X} has no file-backed data")
                return raw_offset + relative
        if rva < section_offset:
            return rva
        raise BinaryAuditError(f"RVA 0x{rva:X} is outside PE sections")

    data_directory = optional_offset + directory_offset
    if data_directory + 16 > optional_offset + optional_size:
        export_rva = import_rva = 0
    else:
        export_rva = struct.unpack_from("<I", data, data_directory)[0]
        import_rva = struct.unpack_from("<I", data, data_directory + 8)[0]

    exports: list[str] = []
    if export_rva:
        export_offset = rva_offset(export_rva)
        if export_offset + 40 > len(data):
            raise BinaryAuditError("truncated PE export directory")
        name_count = struct.unpack_from("<I", data, export_offset + 24)[0]
        names_rva = struct.unpack_from("<I", data, export_offset + 32)[0]
        names_offset = rva_offset(names_rva)
        for index in range(name_count):
            name_rva = struct.unpack_from("<I", data, names_offset + index * 4)[0]
            exports.append(_cstring(data, rva_offset(name_rva)))

    dependencies: list[str] = []
    if import_rva:
        descriptor_offset = rva_offset(import_rva)
        for index in range(4096):
            offset = descriptor_offset + index * 20
            if offset + 20 > len(data):
                raise BinaryAuditError("truncated PE import directory")
            descriptor = struct.unpack_from("<IIIII", data, offset)
            if not any(descriptor):
                break
            dependencies.append(_cstring(data, rva_offset(descriptor[3])))
        else:
            raise BinaryAuditError("PE import directory did not terminate")

    return {
        "format": "PE32+" if magic == 0x20B else "PE32",
        "architectures": [PE_ARCHITECTURES.get(machine, f"machine-0x{machine:04X}")],
        "exports": sorted(set(exports)),
        "dependencies": sorted(set(dependencies), key=str.casefold),
        "inspection_method": "pe-tables",
    }


def _elf_details(data: bytes) -> dict[str, Any]:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise BinaryAuditError("not an ELF binary")
    if data[4] != 2 or data[5] != 1:
        raise BinaryAuditError("only little-endian ELF64 binaries are supported")
    machine = struct.unpack_from("<H", data, 18)[0]
    section_offset = struct.unpack_from("<Q", data, 40)[0]
    section_size, section_count = struct.unpack_from("<HH", data, 58)
    if section_size < 64:
        raise BinaryAuditError("invalid ELF64 section-header size")

    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        offset = section_offset + index * section_size
        if offset + 64 > len(data):
            raise BinaryAuditError("truncated ELF section table")
        values = struct.unpack_from("<IIQQQQIIQQ", data, offset)
        sections.append((values[1], values[4], values[5], values[6]))

    def section_bytes(index: int) -> bytes:
        if not 0 <= index < len(sections):
            raise BinaryAuditError(f"invalid ELF section link: {index}")
        _, offset, size, _ = sections[index]
        if offset + size > len(data):
            raise BinaryAuditError("ELF section data is truncated")
        return data[offset : offset + size]

    dependencies: list[str] = []
    exports: list[str] = []
    for section_type, offset, size, link in sections:
        if offset + size > len(data):
            raise BinaryAuditError("ELF section data is truncated")
        if section_type == 6:  # SHT_DYNAMIC
            strings = section_bytes(link)
            for entry_offset in range(offset, offset + size, 16):
                tag, value = struct.unpack_from("<qQ", data, entry_offset)
                if tag == 0:
                    break
                if tag == 1:
                    dependencies.append(_cstring(strings, value))
        elif section_type == 11:  # SHT_DYNSYM
            strings = section_bytes(link)
            for entry_offset in range(offset, offset + size, 24):
                name, info, _other, section_index, _value, _symbol_size = struct.unpack_from(
                    "<IBBHQQ", data, entry_offset
                )
                binding = info >> 4
                if name and section_index and binding in (1, 2):
                    exports.append(_cstring(strings, name))

    return {
        "format": "ELF64",
        "architectures": [ELF_ARCHITECTURES.get(machine, f"machine-{machine}")],
        "exports": sorted(set(exports)),
        "dependencies": sorted(set(dependencies)),
        "inspection_method": "elf-tables",
    }


def _read_uleb128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _index in range(10):
        if offset >= len(data):
            raise BinaryAuditError("truncated ULEB128 value")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7
    raise BinaryAuditError("oversized ULEB128 value")


def _macho_export_trie(data: bytes, offset: int, size: int) -> list[str]:
    if offset < 0 or offset + size > len(data):
        raise BinaryAuditError("Mach-O export trie exceeds slice bounds")
    trie = data[offset : offset + size]
    exports: list[str] = []
    stack: list[tuple[int, str]] = [(0, "")]
    visited: set[tuple[int, str]] = set()
    while stack:
        node_offset, prefix = stack.pop()
        key = (node_offset, prefix)
        if key in visited:
            continue
        visited.add(key)
        terminal_size, cursor = _read_uleb128(trie, node_offset)
        terminal_end = cursor + terminal_size
        if terminal_end > len(trie):
            raise BinaryAuditError("Mach-O export terminal exceeds trie bounds")
        if terminal_size and prefix:
            exports.append(prefix.removeprefix("_"))
        cursor = terminal_end
        if cursor >= len(trie):
            continue
        child_count = trie[cursor]
        cursor += 1
        for _index in range(child_count):
            edge_end = trie.find(b"\0", cursor)
            if edge_end < 0:
                raise BinaryAuditError("unterminated Mach-O export-trie edge")
            edge = trie[cursor:edge_end].decode("utf-8", errors="replace")
            child_offset, cursor = _read_uleb128(trie, edge_end + 1)
            if child_offset >= len(trie):
                raise BinaryAuditError("Mach-O export child exceeds trie bounds")
            stack.append((child_offset, prefix + edge))
    return exports


def _macho_slice_details(data: bytes, offset: int, size: int) -> tuple[list[str], list[str]]:
    end = offset + size
    if offset < 0 or end > len(data) or size < 32:
        raise BinaryAuditError("universal Mach-O slice is truncated")
    magic = data[offset : offset + 4]
    if magic == b"\xCF\xFA\xED\xFE":
        endian = "<"
    elif magic == b"\xFE\xED\xFA\xCF":
        endian = ">"
    else:
        raise BinaryAuditError("universal entry is not a 64-bit Mach-O slice")
    _magic, _cpu, _subtype, _filetype, command_count, command_bytes, _flags, _reserved = (
        struct.unpack_from(f"{endian}IiiIIIII", data, offset)
    )
    command_offset = offset + 32
    command_end = command_offset + command_bytes
    if command_end > end:
        raise BinaryAuditError("Mach-O load commands exceed slice bounds")
    dependencies: list[str] = []
    symbol_table: tuple[int, int, int, int] | None = None
    export_trie: tuple[int, int] | None = None
    dylib_commands = {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}
    for _index in range(command_count):
        if command_offset + 8 > command_end:
            raise BinaryAuditError("truncated Mach-O load command")
        command, command_size = struct.unpack_from(f"{endian}II", data, command_offset)
        if command_size < 8 or command_offset + command_size > command_end:
            raise BinaryAuditError("invalid Mach-O load command size")
        if command in dylib_commands:
            name_offset = struct.unpack_from(f"{endian}I", data, command_offset + 8)[0]
            dependencies.append(_cstring(data, command_offset + name_offset))
        elif command == 0x2:  # LC_SYMTAB
            symbol_table = struct.unpack_from(f"{endian}IIII", data, command_offset + 8)
        elif command in (0x22, 0x80000022):  # LC_DYLD_INFO(_ONLY)
            dyld_info = struct.unpack_from(
                f"{endian}IIIIIIIIII", data, command_offset + 8
            )
            export_trie = (dyld_info[8], dyld_info[9])
        elif command == 0x80000033:  # LC_DYLD_EXPORTS_TRIE
            export_trie = struct.unpack_from(f"{endian}II", data, command_offset + 8)
        command_offset += command_size

    if export_trie is not None:
        export_offset, export_size = export_trie
        exports = _macho_export_trie(data, offset + export_offset, export_size)
    else:
        exports = []
    if not exports and symbol_table is not None:
        symbol_offset, symbol_count, string_offset, string_size = symbol_table
        symbol_offset += offset
        string_offset += offset
        if string_offset + string_size > end:
            raise BinaryAuditError("Mach-O string table exceeds slice bounds")
        strings = data[string_offset : string_offset + string_size]
        for index in range(symbol_count):
            entry_offset = symbol_offset + index * 16
            if entry_offset + 16 > end:
                raise BinaryAuditError("Mach-O symbol table exceeds slice bounds")
            string_index, symbol_type, _section, _description, _value = struct.unpack_from(
                f"{endian}IBBHQ", data, entry_offset
            )
            is_debug = symbol_type & 0xE0
            is_private_external = symbol_type & 0x10
            is_external = symbol_type & 0x01
            symbol_kind = symbol_type & 0x0E
            if (
                string_index
                and not is_debug
                and not is_private_external
                and is_external
                and symbol_kind != 0
            ):
                exports.append(_cstring(strings, string_index).removeprefix("_"))
    return dependencies, exports


def _macho_details(_path: Path, data: bytes) -> dict[str, Any]:
    if len(data) < 8 or data[:4] not in (b"\xCA\xFE\xBA\xBE", b"\xCA\xFE\xBA\xBF"):
        raise BinaryAuditError("expected a big-endian universal Mach-O binary")
    count = struct.unpack_from(">I", data, 4)[0]
    is_fat64 = data[:4] == b"\xCA\xFE\xBA\xBF"
    entry_size = 32 if is_fat64 else 20
    architectures: list[str] = []
    slices: list[tuple[int, int]] = []
    for index in range(count):
        offset = 8 + index * entry_size
        if offset + entry_size > len(data):
            raise BinaryAuditError("truncated universal Mach-O header")
        cpu_type = struct.unpack_from(">I", data, offset)[0]
        architectures.append(MACH_ARCHITECTURES.get(cpu_type, f"cpu-0x{cpu_type:08X}"))
        if is_fat64:
            slice_offset, slice_size = struct.unpack_from(">QQ", data, offset + 8)
        else:
            slice_offset, slice_size = struct.unpack_from(">II", data, offset + 8)
        slices.append((slice_offset, slice_size))

    dependencies: list[str] = []
    exports: list[str] = []
    for slice_offset, slice_size in slices:
        slice_dependencies, slice_exports = _macho_slice_details(
            data, slice_offset, slice_size
        )
        dependencies.extend(slice_dependencies)
        exports.extend(slice_exports)

    return {
        "format": "Mach-O universal",
        "architectures": sorted(set(architectures)),
        "exports": sorted(set(exports)),
        "dependencies": sorted(set(dependencies)),
        "inspection_method": "macho-fat+load-commands+export-trie-or-symtab",
    }


def is_debug_runtime(name: str) -> bool:
    lowered = Path(name).name.casefold()
    return (
        lowered in {"ucrtbased.dll", "msvcp140d.dll", "vcruntime140d.dll"}
        or lowered.startswith("msvcp") and lowered.endswith("d.dll")
        or lowered.startswith("vcruntime") and lowered.endswith("d.dll")
    )


def audit_binary(
    path: Path,
    *,
    expected_architectures: set[str],
    required_export: str = "gdext_rust_init",
    require_complete_dependency_scan: bool = True,
) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise BinaryAuditError(f"binary does not exist: {path}")
    data = path.read_bytes()
    if data.startswith(b"MZ"):
        details = _pe_details(data)
    elif data.startswith(b"\x7fELF"):
        details = _elf_details(data)
    elif data[:4] in (b"\xCA\xFE\xBA\xBE", b"\xCA\xFE\xBA\xBF"):
        details = _macho_details(path, data)
    else:
        raise BinaryAuditError("unsupported native binary format")

    failures: list[str] = []
    actual_architectures = set(details["architectures"])
    if actual_architectures != expected_architectures:
        failures.append(
            "architectures do not match: "
            f"expected {sorted(expected_architectures)}, found {sorted(actual_architectures)}"
        )
    if required_export not in details["exports"]:
        failures.append(f"required export is missing: {required_export}")
    debug_dependencies = [
        dependency for dependency in details["dependencies"] if is_debug_runtime(dependency)
    ]
    if debug_dependencies:
        failures.append(f"debug runtime dependencies found: {debug_dependencies}")

    all_exports = details.pop("exports")
    selected_exports = [name for name in all_exports if name == required_export]

    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "binary": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        **details,
        "exports": selected_exports,
        "export_count": len(all_exports),
        "other_export_count": len(all_exports) - len(selected_exports),
        "debug_runtime_dependencies": debug_dependencies,
        "required_export": required_export,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--architecture", action="append", required=True)
    parser.add_argument("--required-export", default="gdext_rust_init")
    parser.add_argument("--allow-header-only-macho", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_binary(
            args.binary,
            expected_architectures=set(args.architecture),
            required_export=args.required_export,
            require_complete_dependency_scan=not args.allow_header_only_macho,
        )
    except (BinaryAuditError, OSError, struct.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
