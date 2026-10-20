# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Small file-backed native tables for binary audit regression tests."""

import struct


def pe_binary(dependency=None, *, delay=False, va=False, dll=True, exports=("gdext_rust_init",)):
    data = bytearray(0x1000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<HH", data, 0x94, 0xF0, 0x2002 if dll else 2)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<Q", data, 0x98 + 24, 0x10000000)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    struct.pack_into("<IIII", data, 0x188 + 8, 0xE00, 0x1000, 0xE00, 0x200)
    directories = 0x98 + 112
    struct.pack_into("<II", data, directories, 0x1000, 40)
    struct.pack_into("<II", data, 0x200 + 20, len(exports), len(exports))
    struct.pack_into("<III", data, 0x200 + 28, 0x1060, 0x1040, 0x1070)
    struct.pack_into("<I", data, 0x260, 0x1200)
    assert len(exports) <= 8
    cursor = 0x280
    for index, name in enumerate(exports):
        raw = name.encode() + b"\0"
        assert cursor + len(raw) <= 0x300
        struct.pack_into("<I", data, 0x240 + index * 4, 0x1000 + cursor - 0x200)
        data[cursor:cursor + len(raw)] = raw
        cursor += len(raw)
    if dependency:
        raw = dependency.encode() + b"\0"
        data[0x380:0x380 + len(raw)] = raw
        if delay:
            struct.pack_into("<II", data, directories + 13 * 8, 0x1100, 64)
            struct.pack_into("<8I", data, 0x300, 0 if va else 1, 0x10001180 if va else 0x1180, 0, 0x11C0, 0x11E0, 0, 0, 0)
        else:
            struct.pack_into("<II", data, directories + 8, 0x1100, 40)
            struct.pack_into("<5I", data, 0x300, 0x11C0, 0, 0, 0x1180, 0x11E0)
    return data


def elf_binary(dependency="libc.so.6", *, search_path=None, shared=True, exports=("gdext_rust_init",)):
    data = bytearray(0x800)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", data, 16, 3 if shared else 2, 62)
    struct.pack_into("<QQ", data, 32, 64, 0x500)
    struct.pack_into("<HHHH", data, 54, 56, 2, 64, 3)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0x1000, 0, len(data), len(data), 1)
    strings = bytearray(b"\0")
    def string(text):
        offset = len(strings)
        strings.extend(text.encode() + b"\0")
        return offset
    needed = string(dependency)
    exported = [string(name) for name in exports]
    assert len(exported) <= 9
    tags = [(1, needed), (5, 0x1400)]
    if search_path is not None:
        tags.append((29, string(search_path)))
    tags += [(10, len(strings)), (0, 0)]
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 4, 0x200, 0x1200, 0, len(tags) * 16, len(tags) * 16, 8)
    for index, values in enumerate(tags):
        struct.pack_into("<qQ", data, 0x200 + index * 16, *values)
    data[0x400:0x400 + len(strings)] = strings
    for index, name in enumerate(exported):
        struct.pack_into("<IBBHQQ", data, 0x318 + index * 24, name, 0x12, 0, 1, 0x1000, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 0x540, 0, 3, 0, 0, 0x400, len(strings), 0, 0, 1, 0)
    symbols_size = 24 * (1 + len(exported))
    struct.pack_into("<IIQQQQIIQQ", data, 0x580, 0, 11, 0, 0, 0x300, symbols_size, 1, 0, 8, 24)
    return data


def macho_slice(cpu, *, export=True, filetype=6, dependency=None, rpath=None, install_name=None, trie=False, exports=None):
    names = list(exports) if exports is not None else (["gdext_rust_init"] if export else [])
    data = bytearray(512)
    commands = []
    for command, name, minimum in [(0xC, dependency, 24), (0x8000001C, rpath, 12), (0xD, install_name, 24)]:
        if name is not None:
            raw = name.encode() + b"\0"
            size = (minimum + len(raw) + 7) // 8 * 8
            payload = bytearray(size)
            struct.pack_into("<III", payload, 0, command, size, minimum)
            payload[minimum:minimum + len(raw)] = raw
            commands.append(payload)
    if trie:
        # Root node with one edge per export; each child is a two-byte terminal.
        edges = [b"_" + name.encode() for name in names]
        child = 2 + sum(len(edge) + 2 for edge in edges)
        raw = b"\0" + bytes([len(edges)])
        for index, edge in enumerate(edges):
            assert child + index * 4 < 0x80
            raw += edge + b"\0" + bytes([child + index * 4])
        raw += b"\x02\0\0\0" * len(edges)
        commands.append(struct.pack("<4I", 0x80000033, 16, 256, len(raw)))
        data[256:256 + len(raw)] = raw
    else:
        strings_offset = 256 + 16 * max(1, len(names))
        strings = b"\0" + b"".join(b"_" + name.encode() + b"\0" for name in names)
        strings_size = max(64, len(strings))
        assert strings_offset + strings_size <= len(data)
        commands.append(struct.pack("<6I", 2, 24, 256, len(names), strings_offset, strings_size))
        cursor = 1
        for index, name in enumerate(names):
            struct.pack_into("<IBBHQ", data, 256 + index * 16, cursor, 0xF, 1, 0, 0x1000)
            cursor += len(name) + 2
        data[strings_offset:strings_offset + len(strings)] = strings
    command_data = b"".join(commands)
    struct.pack_into("<8I", data, 0, 0xFEEDFACF, cpu, 0, filetype, len(commands), len(command_data), 0, 0)
    data[32:32 + len(command_data)] = command_data
    return data


def fat_binary(first=None, second=None, *, fat64=False):
    first = macho_slice(0x01000007) if first is None else first
    second = macho_slice(0x0100000C) if second is None else second
    entry_size = 32 if fat64 else 20
    header_size = 8 + entry_size * 2
    data = bytearray(header_size)
    struct.pack_into(">II", data, 0, 0xCAFEBABF if fat64 else 0xCAFEBABE, 2)
    offset = header_size
    for index, (cpu, payload) in enumerate([(0x01000007, first), (0x0100000C, second)]):
        if fat64:
            struct.pack_into(">IIQQII", data, 8 + index * entry_size, cpu, 0, offset, len(payload), 0, 0)
        else:
            struct.pack_into(">5I", data, 8 + index * entry_size, cpu, 0, offset, len(payload), 0)
        offset += len(payload)
    return data + first + second
