# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Audit GDExtension architecture, exports, and native dependencies."""

from __future__ import annotations

import argparse
import json
import re
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
    optional_size, characteristics = struct.unpack_from("<HH", data, pe_offset + 20)
    optional_offset = pe_offset + 24
    if optional_size < 2 or optional_offset + optional_size > len(data):
        raise BinaryAuditError("truncated PE optional header")
    magic = struct.unpack_from("<H", data, optional_offset)[0]
    directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
    if directory_offset is None or optional_size < directory_offset:
        raise BinaryAuditError("unsupported or truncated PE optional header")
    image_base = struct.unpack_from(
        "<Q" if magic == 0x20B else "<I",
        data,
        optional_offset + (24 if magic == 0x20B else 28),
    )[0]
    directory_count = struct.unpack_from("<I", data, optional_offset + directory_offset - 4)[0]
    if directory_offset + directory_count * 8 > optional_size:
        raise BinaryAuditError("PE data directories exceed optional header")
    section_offset = optional_offset + optional_size
    sections: list[tuple[int, int, int]] = []
    for index in range(section_count):
        offset = section_offset + index * 40
        if offset + 40 > len(data):
            raise BinaryAuditError("truncated PE section table")
        _virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", data, offset + 8
        )
        if raw_offset + raw_size > len(data):
            raise BinaryAuditError("truncated PE section data")
        sections.append((virtual_address, raw_offset, raw_size))

    def rva_data(rva: int, size: int = 1) -> bytes:
        for address, offset, span in sections:
            if address <= rva and rva + size <= address + span:
                start = offset + rva - address
                return data[start : offset + span] if size == 1 else data[start : start + size]
        raise BinaryAuditError("PE RVA exceeds file-backed section bounds")

    def directory(index: int) -> tuple[int, int]:
        if index >= directory_count:
            return 0, 0
        rva, size = struct.unpack_from("<II", data, optional_offset + directory_offset + index * 8)
        if bool(rva) != bool(size):
            raise BinaryAuditError("inconsistent PE data directory")
        return rva, size

    exports: list[str] = []
    export_rva, export_size = directory(0)
    if export_rva:
        if export_size < 40:
            raise BinaryAuditError("truncated PE export directory")
        table = rva_data(export_rva, export_size)
        name_count, = struct.unpack_from("<I", table, 24)
        names_rva, = struct.unpack_from("<I", table, 32)
        if name_count:
            names = rva_data(names_rva, name_count * 4)
            for index in range(name_count):
                name_rva, = struct.unpack_from("<I", names, index * 4)
                exports.append(_cstring(rva_data(name_rva), 0))

    def imports(index: int, descriptor_size: int, delayed: bool = False) -> list[str]:
        rva, size = directory(index)
        if not rva:
            return []
        table = rva_data(rva, size)
        names = []
        for offset in range(0, size - descriptor_size + 1, descriptor_size):
            descriptor = struct.unpack_from("<8I" if delayed else "<5I", table, offset)
            if not any(descriptor):
                return names
            name_rva = descriptor[1 if delayed else 3]
            if delayed:
                attributes = descriptor[0]
                if attributes not in (0, 1):
                    raise BinaryAuditError("unsupported PE delay-import attributes")
                if not attributes:
                    if name_rva < image_base:
                        raise BinaryAuditError("invalid PE delay-import VA")
                    name_rva -= image_base
            names.append(_cstring(rva_data(name_rva), 0))
        raise BinaryAuditError("PE import directory did not terminate within bounds")

    normal_imports = imports(1, 20)
    delay_imports = imports(13, 32, True)
    return {
        "format": "PE32+" if magic == 0x20B else "PE32",
        "object_type": "DLL" if characteristics & 0x2000 else "non-DLL",
        "architectures": [PE_ARCHITECTURES.get(machine, f"machine-0x{machine:04X}")],
        "exports": sorted(set(exports)),
        "dependencies": sorted(set(normal_imports + delay_imports), key=str.casefold),
        "delay_dependencies": sorted(set(delay_imports), key=str.casefold),
        "dependency_scan_complete": directory_count >= 14,
        "inspection_method": "pe-import+delay-import-tables",
    }


def _elf_details(data: bytes) -> dict[str, Any]:
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise BinaryAuditError("not an ELF binary")
    if data[4] != 2 or data[5] != 1:
        raise BinaryAuditError("only little-endian ELF64 binaries are supported")
    object_type, machine = struct.unpack_from("<HH", data, 16)
    program_offset, section_offset = struct.unpack_from("<QQ", data, 32)
    program_size, program_count, section_size, section_count = struct.unpack_from(
        "<HHHH", data, 54
    )
    if (section_count and section_size < 64) or (program_count and program_size < 56):
        raise BinaryAuditError("invalid ELF64 table entry size")
    loads = []
    dynamics = []
    for index in range(program_count):
        offset = program_offset + index * program_size
        if offset + 56 > len(data):
            raise BinaryAuditError("truncated ELF program table")
        kind, _flags, file_offset, address, _physical, size, _memory, _alignment = (
            struct.unpack_from("<IIQQQQQQ", data, offset)
        )
        if file_offset + size > len(data):
            raise BinaryAuditError("truncated ELF segment")
        if kind == 1:
            loads.append((address, file_offset, size))
        elif kind == 2:
            dynamics.append((file_offset, size))
    if len(dynamics) > 1:
        raise BinaryAuditError("duplicate ELF dynamic segments")
    dependencies = []
    search_paths = []
    complete = False
    for offset, size in dynamics:
        tags: dict[int, list[int]] = {}
        for cursor in range(offset, offset + size - 15, 16):
            tag, value = struct.unpack_from("<qQ", data, cursor)
            if tag == 0:
                complete = True
                break
            tags.setdefault(tag, []).append(value)
        if not complete:
            raise BinaryAuditError("ELF dynamic segment did not terminate")
        if len(tags.get(5, [])) != 1 or len(tags.get(10, [])) != 1:
            raise BinaryAuditError("ELF dynamic string table is missing or ambiguous")
        address, length = tags[5][0], tags[10][0]
        strings = None
        for base, file_offset, span in loads:
            if base <= address and address + length <= base + span:
                start = file_offset + address - base
                strings = data[start : start + length]
                break
        if strings is None:
            raise BinaryAuditError("ELF dynamic strings exceed load-segment bounds")
        dependencies = [
            _cstring(strings, value)
            # NEEDED, AUXILIARY, FILTER, DEPAUDIT, AUDIT
            for tag in (1, 0x7FFFFFFD, 0x7FFFFFFF, 0x6FFFFEFB, 0x6FFFFEFC)
            for value in tags.get(tag, [])
        ]
        search_paths = [
            _cstring(strings, value) for tag in (15, 29) for value in tags.get(tag, [])
        ]

    sections = []
    for index in range(section_count):
        offset = section_offset + index * section_size
        if offset + 64 > len(data):
            raise BinaryAuditError("truncated ELF section table")
        values = struct.unpack_from("<IIQQQQIIQQ", data, offset)
        sections.append((values[1], values[4], values[5], values[6]))

    def section_bytes(index: int) -> bytes:
        if not 0 <= index < len(sections):
            raise BinaryAuditError("invalid ELF section link")
        _, offset, size, _ = sections[index]
        if offset + size > len(data):
            raise BinaryAuditError("ELF section data is truncated")
        return data[offset : offset + size]

    exports = []
    for kind, offset, size, link in sections:
        if kind != 11:  # SHT_DYNSYM
            continue
        if offset + size > len(data) or size % 24:
            raise BinaryAuditError("invalid ELF dynamic symbol table")
        strings = section_bytes(link)
        for cursor in range(offset, offset + size, 24):
            name, info, other, section_index, _value, _symbol_size = struct.unpack_from(
                "<IBBHQQ", data, cursor
            )
            if name and section_index and (info >> 4) in (1, 2) and (other & 3) in (0, 3):
                exports.append(_cstring(strings, name))
    return {
        "format": "ELF64",
        "object_type": "ET_DYN" if object_type == 3 else f"ELF-type-{object_type}",
        "architectures": [ELF_ARCHITECTURES.get(machine, f"machine-{machine}")],
        "exports": sorted(set(exports)),
        "dependencies": sorted(set(dependencies)),
        "search_paths": search_paths,
        "dependency_scan_complete": complete,
        "inspection_method": "elf-program+symbol-tables",
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
    visited: set[int] = set()
    while stack:
        node_offset, prefix = stack.pop()
        if node_offset in visited:
            raise BinaryAuditError("cyclic or overlapping Mach-O export trie")
        visited.add(node_offset)
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


def _macho_slice_details(data: bytes, offset: int, size: int) -> dict[str, Any]:
    if offset < 0 or offset + size > len(data) or size < 32:
        raise BinaryAuditError("universal Mach-O slice is truncated")
    data = data[offset : offset + size]
    if data[:4] == b"\xCF\xFA\xED\xFE":
        endian = "<"
    elif data[:4] == b"\xFE\xED\xFA\xCF":
        endian = ">"
    else:
        raise BinaryAuditError("universal entry is not a 64-bit Mach-O slice")
    _magic, cpu, subtype, filetype, command_count, command_bytes, _flags, _reserved = (
        struct.unpack_from(f"{endian}8I", data)
    )
    command_offset = 32
    command_end = command_offset + command_bytes
    if command_end > len(data):
        raise BinaryAuditError("Mach-O load commands exceed slice bounds")
    dependencies = []
    search_paths = []
    install_names = []
    symbol_table = None
    export_trie = None
    dylib_commands = {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}
    for _index in range(command_count):
        if command_offset + 8 > command_end:
            raise BinaryAuditError("truncated Mach-O load command")
        command, command_size = struct.unpack_from(f"{endian}II", data, command_offset)
        if command_size < 8 or command_offset + command_size > command_end:
            raise BinaryAuditError("invalid Mach-O load command size")
        payload = data[command_offset : command_offset + command_size]
        if command in dylib_commands | {0xD, 0x8000001C}:
            minimum = 12 if command == 0x8000001C else 24
            if command_size < minimum:
                raise BinaryAuditError("truncated Mach-O path command")
            name_offset, = struct.unpack_from(f"{endian}I", payload, 8)
            if name_offset < minimum:
                raise BinaryAuditError("invalid Mach-O path offset")
            name = _cstring(payload, name_offset)
            if command == 0xD:
                install_names.append(name)
            elif command == 0x8000001C:
                search_paths.append(name)
            else:
                dependencies.append(name)
        elif command == 0x2:  # LC_SYMTAB
            if command_size < 24 or symbol_table is not None:
                raise BinaryAuditError("invalid Mach-O symbol-table command")
            symbol_table = struct.unpack_from(f"{endian}4I", payload, 8)
        elif command in (0x22, 0x80000022):  # LC_DYLD_INFO(_ONLY)
            if command_size < 48:
                raise BinaryAuditError("truncated Mach-O dyld-info command")
            dyld_info = struct.unpack_from(f"{endian}10I", payload, 8)
            export_trie = (dyld_info[8], dyld_info[9])
        elif command == 0x80000033:  # LC_DYLD_EXPORTS_TRIE
            if command_size < 16:
                raise BinaryAuditError("truncated Mach-O export-trie command")
            export_trie = struct.unpack_from(f"{endian}II", payload, 8)
        command_offset += command_size
    if command_offset != command_end:
        raise BinaryAuditError("Mach-O load-command count does not match size")

    exports = []
    has_export_trie = export_trie is not None and export_trie[1] > 0
    if has_export_trie:
        exports = _macho_export_trie(data, *export_trie)
    if symbol_table is not None:
        symbol_offset, symbol_count, string_offset, string_size = symbol_table
        if (
            string_offset + string_size > len(data)
            or symbol_offset + symbol_count * 16 > len(data)
        ):
            raise BinaryAuditError("Mach-O symbol or string table exceeds slice bounds")
        if not has_export_trie:
            strings = data[string_offset : string_offset + string_size]
            for index in range(symbol_count):
                string_index, symbol_type, _section, _description, _value = (
                    struct.unpack_from(f"{endian}IBBHQ", data, symbol_offset + index * 16)
                )
                if (
                    string_index
                    and not symbol_type & 0xF0
                    and symbol_type & 0x01
                    and symbol_type & 0x0E
                ):
                    exports.append(_cstring(strings, string_index).removeprefix("_"))
    return {
        "cpu_type": cpu,
        "cpu_subtype": subtype,
        "architecture": MACH_ARCHITECTURES.get(cpu, f"cpu-0x{cpu:08X}"),
        "object_type": "MH_DYLIB" if filetype == 6 else f"Mach-O-type-{filetype}",
        "dependencies": sorted(set(dependencies)),
        "exports": sorted(set(exports)),
        "search_paths": search_paths,
        "install_names": install_names,
        "dependency_scan_complete": True,
    }


def _macho_details(_path: Path, data: bytes) -> dict[str, Any]:
    if len(data) < 8 or data[:4] not in (b"\xCA\xFE\xBA\xBE", b"\xCA\xFE\xBA\xBF"):
        raise BinaryAuditError("expected a big-endian universal Mach-O binary")
    count = struct.unpack_from(">I", data, 4)[0]
    is_fat64 = data[:4] == b"\xCA\xFE\xBA\xBF"
    entry_size = 32 if is_fat64 else 20
    header_end = 8 + count * entry_size
    if not 1 <= count <= 32 or header_end > len(data):
        raise BinaryAuditError("invalid or truncated universal Mach-O header")
    slices = []
    ranges = []
    cpus = set()
    for index in range(count):
        offset = 8 + index * entry_size
        cpu, subtype = struct.unpack_from(">II", data, offset)
        if cpu in cpus:
            raise BinaryAuditError("duplicate universal Mach-O architecture")
        cpus.add(cpu)
        if is_fat64:
            slice_offset, slice_size, alignment = struct.unpack_from(">QQI", data, offset + 8)
        else:
            slice_offset, slice_size, alignment = struct.unpack_from(">III", data, offset + 8)
        end = slice_offset + slice_size
        if slice_offset < header_end or slice_size < 32 or end > len(data):
            raise BinaryAuditError("universal Mach-O slice exceeds payload bounds")
        if alignment > 63 or slice_offset % (1 << alignment):
            raise BinaryAuditError("invalid universal Mach-O slice alignment")
        if any(
            slice_offset < other_end and other_start < end
            for other_start, other_end in ranges
        ):
            raise BinaryAuditError("overlapping universal Mach-O slices")
        ranges.append((slice_offset, end))
        details = _macho_slice_details(data, slice_offset, slice_size)
        if (details["cpu_type"], details["cpu_subtype"]) != (cpu, subtype):
            raise BinaryAuditError("universal Mach-O CPU label differs from slice header")
        slices.append({"offset": slice_offset, "size": slice_size, **details})
    return {
        "format": "Mach-O universal",
        "object_type": (
            "MH_DYLIB" if all(row["object_type"] == "MH_DYLIB" for row in slices)
            else "non-dylib slice"
        ),
        "architectures": sorted(row["architecture"] for row in slices),
        "exports": sorted({name for row in slices for name in row["exports"]}),
        "dependencies": sorted({name for row in slices for name in row["dependencies"]}),
        "search_paths": sorted({name for row in slices for name in row["search_paths"]}),
        "install_names": sorted({name for row in slices for name in row["install_names"]}),
        "slices": slices,
        "dependency_scan_complete": all(row["dependency_scan_complete"] for row in slices),
        "inspection_method": "macho-fat+load-commands+export-trie-or-symtab",
    }


# Pure-Rust extensions built with the pinned toolchain import these release/system
# libraries. New native dependencies require an explicit policy change; this
# package bundles none. On Windows, Rust std reaches the UCRT only through the
# heap, runtime, string and (for floating-point math) math API sets; the other
# api-ms-win-crt-* sets come from C/C++ code and stay rejected until a consumer
# needs one.
SYSTEM_DEPENDENCIES = {
    "windows": {
        "api-ms-win-core-synch-l1-2-0.dll",
        "api-ms-win-crt-heap-l1-1-0.dll",
        "api-ms-win-crt-math-l1-1-0.dll",
        "api-ms-win-crt-runtime-l1-1-0.dll",
        "api-ms-win-crt-string-l1-1-0.dll",
        "bcryptprimitives.dll", "kernel32.dll", "ntdll.dll", "vcruntime140.dll",
    },
    "linux": {"ld-linux-x86-64.so.2", "libc.so.6", "libgcc_s.so.1"},
    "macos": {"/usr/lib/libSystem.B.dylib"},
}
# Exports the pinned gdext revision adds next to the entry symbol. On Linux its
# #[gdextension] macro interposes glibc's TLS-destructor hook so hot reload can
# unload the library (godot-ffi `register_hot_reload_workaround!`).
FRAMEWORK_EXPORTS = {
    "windows": set(),
    "linux": {"__cxa_thread_atexit_impl"},
    "macos": set(),
}
# Unexpected export names are listed only when they are short C identifiers;
# anything else (mangled or malformed names) is counted, never echoed.
REPORTABLE_EXPORT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
MAX_LISTED_EXPORTS = 16
TARGET_FORMATS = {
    "windows-x86_64": {"PE32+"},
    "linux-x86_64": {"ELF64"},
    "macos-universal": {"Mach-O universal"},
}


def is_debug_runtime(name: str) -> bool:
    lowered = name.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return (
        lowered in {"ucrtbased.dll", "msvcp140d.dll", "vcruntime140d.dll"}
        or lowered.startswith("msvcp") and lowered.endswith("d.dll")
        or lowered.startswith("vcruntime") and lowered.endswith("d.dll")
    )


def _relative_loader_path(name: str, tokens: tuple[str, ...]) -> bool:
    parts = name.split("/")
    return (
        parts[0] in tokens
        and all(part not in ("", ".", "..") for part in parts)
        and "\\" not in name
        and ":" not in name
    )


def audit_binary(
    path: Path,
    *,
    expected_architectures: set[str],
    expected_target: str | None = None,
    required_export: str = "gdext_rust_init",
    exact_exports: bool = False,
) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise BinaryAuditError("binary does not exist")
    data = path.read_bytes()
    if data.startswith(b"MZ"):
        details = _pe_details(data)
        platform, shared_type = "windows", "DLL"
    elif data.startswith(b"\x7fELF"):
        details = _elf_details(data)
        platform, shared_type = "linux", "ET_DYN"
    elif data[:4] in (b"\xCA\xFE\xBA\xBE", b"\xCA\xFE\xBA\xBF"):
        details = _macho_details(path, data)
        platform, shared_type = "macos", "MH_DYLIB"
    else:
        raise BinaryAuditError("unsupported native binary format")

    failures: list[str] = []
    if expected_target is not None:
        if expected_target not in TARGET_FORMATS:
            raise BinaryAuditError("unsupported target dependency policy")
        if details["format"] not in TARGET_FORMATS[expected_target]:
            failures.append("binary format does not match target")
    if details["object_type"] != shared_type:
        failures.append(f"binary is not a shared library ({shared_type})")
    actual_architectures = set(details["architectures"])
    if actual_architectures != expected_architectures:
        failures.append(
            "architectures do not match: "
            f"expected {sorted(expected_architectures)}, found {sorted(actual_architectures)}"
        )
    if not details["dependency_scan_complete"]:
        failures.append("native dependency scan is incomplete")
    slices = details.get("slices", [details])
    permitted = {required_export} | FRAMEWORK_EXPORTS[platform]
    unexpected: set[str] = set()
    for row in slices:
        architecture = row.get("architecture", ",".join(details["architectures"]))
        if required_export not in row["exports"]:
            failures.append(f"required export is missing in {architecture}: {required_export}")
        extra = sorted(set(row["exports"]) - permitted)
        if exact_exports and extra:
            failures.append(f"unexpected exports in {architecture}: {len(extra)}")
        if "slices" in details:
            row["unexpected_export_count"] = len(extra)
        unexpected.update(extra)
    debug_dependencies = [name for name in details["dependencies"] if is_debug_runtime(name)]
    if debug_dependencies:
        failures.append("debug runtime dependencies found")
    allowed = SYSTEM_DEPENDENCIES[platform]
    for dependency in details["dependencies"]:
        key = dependency.casefold() if platform == "windows" else dependency
        if key not in allowed:
            failures.append("non-system dependency is not bundled by this package")
    for search_path in details.get("search_paths", []):
        tokens = ("$ORIGIN", "${ORIGIN}") if platform == "linux" else ("@loader_path",)
        for entry in search_path.split(":" if platform == "linux" else "\0"):
            if not _relative_loader_path(entry, tokens):
                failures.append("nonportable native library search path")
    for name in details.get("install_names", []):
        if not _relative_loader_path(name, ("@rpath", "@loader_path")) and (
            "/" in name or "\\" in name or ":" in name or not name
        ):
            failures.append("nonportable dylib install name")

    all_exports = details.pop("exports")
    selected_exports = [name for name in all_exports if name == required_export]
    for row in details.get("slices", []):
        exports = row["exports"]
        row["exports"] = [name for name in exports if name == required_export]
        row["export_count"] = len(exports)
    return {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "binary": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        **details,
        "dependency_policy": platform,
        "expected_target": expected_target,
        "exports": selected_exports,
        "export_count": len(all_exports),
        "other_export_count": len(all_exports) - len(selected_exports),
        "debug_runtime_dependencies": debug_dependencies,
        "required_export": required_export,
        "exact_exports": exact_exports,
        "unexpected_export_count": len(unexpected),
        "unexpected_exports": sorted(
            name for name in unexpected if REPORTABLE_EXPORT.fullmatch(name)
        )[:MAX_LISTED_EXPORTS],
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--architecture", action="append", required=True)
    parser.add_argument("--target", choices=sorted(TARGET_FORMATS))
    parser.add_argument("--required-export", default="gdext_rust_init")
    parser.add_argument(
        "--exact-exports",
        action="store_true",
        help="fail when any export other than the entry symbol (and the pinned "
        "gdext framework exports) is present, per slice",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_binary(
            args.binary,
            expected_architectures=set(args.architecture),
            expected_target=args.target,
            required_export=args.required_export,
            exact_exports=args.exact_exports,
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
