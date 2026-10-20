# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import builtins
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from tests.binary_fixtures import elf_binary, pe_binary
from tests.test_packaging import minimal_pe
from tools.package_release import PackageError, _validate_payload, create_package


def compressed_elf(packed: bytes, *, legacy: bool = False) -> bytes:
    """File-backed ELF64 with an exported entry point and debug section."""
    data = bytearray(0x700)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HHI", data, 16, 3, 62, 1)
    struct.pack_into("<Q", data, 40, 0x500)
    struct.pack_into("<HHH", data, 58, 64, 5, 4)
    strings = b"\0gdext_rust_init\0"
    data[0x200:0x200 + len(strings)] = strings
    struct.pack_into("<IBBHQQ", data, 0x300 + 24, 1, 0x12, 0, 1, 1, 0)
    names = b"\0.strtab\0.dynsym\0" + (b".zdebug_str" if legacy else b".debug_str") + b"\0.shstrtab\0"
    data[0x400:0x400 + len(names)] = names
    struct.pack_into("<IIQQQQIIQQ", data, 0x540, 1, 3, 0, 0, 0x200, len(strings), 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 0x580, 9, 11, 0, 0, 0x300, 48, 1, 0, 8, 24)
    struct.pack_into("<IIQQQQIIQQ", data, 0x5c0, 17, 1, 0 if legacy else 0x800, 0, len(data), len(packed), 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 0x600, names.index(b".shstrtab"), 3, 0, 0, 0x400, len(names), 0, 0, 1, 0)
    return bytes(data) + packed


def elf_header(payload: bytes, kind: int = 1) -> bytes:
    return struct.pack("<IIQQ", kind, 0, len(payload), 1)


def compressed_macho() -> bytes:
    data = bytearray(32 + 72 + 80)
    struct.pack_into("<IiiIIIII", data, 0, 0xfeedfacf, 0x1000007, 3, 6, 1, 152, 0, 0)
    struct.pack_into("<II", data, 32, 0x19, 152)
    struct.pack_into("<I", data, 32 + 64, 1)
    data[104:120] = b"__zdebug_str\0\0\0\0\0"
    return bytes(data)


class PackagePrivacyTests(unittest.TestCase):
    def test_unicode_first_components_in_all_encodings_and_alignments(self) -> None:
        for component in ("\u4e2d\u6587", "\u00e9quipe", "\U0001f4c1", "\u4e2d\u6587 folder"):
            for prefix in ("q:/", "Q:/", "q:\\", "Q:\\", "/"):
                separator = "\\" if "\\" in prefix else "/"
                marker = prefix + component + separator + "private" + separator + "source.rs"
                for encoding in ("utf-8", "utf-16le", "utf-16be"):
                    for offset in (b"", b"\xff"):
                        for name in ("smoke.dll", "smoke.so", "smoke.dylib", "LICENSE"):
                            with self.subTest(component=component, prefix=prefix, encoding=encoding, offset=offset, name=name):
                                data = offset + ("\0" + marker + "\0").encode(encoding)
                                if prefix == "/" and encoding != "utf-8" and name != "LICENSE":
                                    # Wide POSIX roots in native libraries are indistinguishable
                                    # from decoded numeric data; only UTF-8 ones are checked.
                                    _validate_payload(name, data)
                                    continue
                                with self.assertRaisesRegex(PackageError, "private absolute path") as caught:
                                    _validate_payload(name, data)
                                self.assertNotIn(marker, str(caught.exception))

    def test_unicode_instruction_fragments_are_not_posix_paths(self) -> None:
        # UTF-16 decoding of ordinary instruction bytes can resemble a root.
        fragments = (".\u7400\u37b0\ue000.\u2400/\u8400\u37b0\u3000/\u4e00/\u9400\u37b0\u5000/\u9600/",
                     "\u2005/\uffff\uffff\u2043/", "\u2e8e/\u8948\u2444\u4848\u058b\u2e82/\u0548\u1328",
                     "/\u7275\u7374\u632f\u3461\u3465\u6634")
        for fragment in fragments:
            for encoding in ("utf-16le", "utf-16be"):
                _validate_payload("smoke.dll", ("\0" + fragment + "\0").encode(encoding))

    def test_drive_root_does_not_require_a_directory_character(self) -> None:
        for marker in ("q:/", "Q:\\", "q:/\u4e2d"):
            for encoding in ("utf-8", "utf-16le", "utf-16be"):
                with self.subTest(marker=marker, encoding=encoding):
                    with self.assertRaisesRegex(PackageError, "private absolute path"):
                        _validate_payload("smoke.dll", ("\0" + marker + "\0").encode(encoding))

    def test_elf_zlib_and_legacy_sections_are_scanned(self) -> None:
        for marker in (b"Q:/fictional/private/source.rs\0", "q:/\u4e2d\u6587/private/source.rs\0".encode()):
            for legacy in (False, True):
                for payload, reject in ((marker, True), (b"__source/src/lib.rs\0", False)):
                    header = b"ZLIB" + len(payload).to_bytes(8, "big") if legacy else elf_header(payload)
                    data = compressed_elf(header + zlib.compress(payload), legacy=legacy)
                    with self.subTest(legacy=legacy, reject=reject, marker=marker):
                        if reject:
                            with self.assertRaisesRegex(PackageError, "private absolute path"):
                                _validate_payload("smoke.so", data)
                        else:
                            _validate_payload("smoke.so", data)

    def test_bad_or_unsupported_compressed_sections_reject(self) -> None:
        payload = b"__source/src/lib.rs\0"
        for packed in (elf_header(payload, 99) + zlib.compress(payload),
                       elf_header(payload) + b"corrupt",
                       elf_header(payload + b"x") + zlib.compress(payload),
                       elf_header(payload) + zlib.compress(payload) + b"trailing",
                       struct.pack("<IIQQ", 1, 0, 256 * 1024 * 1024 + 1, 1), b"short"):
            with self.subTest(packed=packed):
                with self.assertRaises(PackageError):
                    _validate_payload("smoke.so", compressed_elf(packed))
        with self.assertRaisesRegex(PackageError, "unsupported legacy"):
            _validate_payload("smoke.so", compressed_elf(b"not-zlib", legacy=True))

    def test_elf_compressed_mini_debug_rejects(self) -> None:
        data = bytearray(compressed_elf(b"opaque", legacy=True))
        # Give the section-name table enough room for the longer name.
        names = b"\0.strtab\0.dynsym\0.gnu_debugdata\0.shstrtab\0"
        data[0x400:0x400 + len(names)] = names
        struct.pack_into("<Q", data, 0x600 + 32, len(names))
        struct.pack_into("<I", data, 0x600, names.index(b".shstrtab"))
        with self.assertRaisesRegex(PackageError, "mini-debug"):
            _validate_payload("smoke.so", bytes(data))

    def test_elf_zstd_uses_only_optional_stdlib_support(self) -> None:
        original_import = builtins.__import__

        def no_zstd(name, *args, **kwargs):
            if name == "compression":
                raise ImportError("unavailable")
            return original_import(name, *args, **kwargs)

        payload = b"Q:/fictional/private/source.rs\0"
        with patch("builtins.__import__", side_effect=no_zstd):
            with self.assertRaisesRegex(PackageError, "stdlib support"):
                _validate_payload("smoke.so", compressed_elf(elf_header(payload, 2) + b"opaque"))
        try:
            from compression import zstd
        except ImportError:
            return
        with self.assertRaisesRegex(PackageError, "private absolute path"):
            _validate_payload("smoke.so", compressed_elf(elf_header(payload, 2) + zstd.compress(payload)))
        clean = b"__source/src/lib.rs\0"
        _validate_payload("smoke.so", compressed_elf(elf_header(clean, 2) + zstd.compress(clean)))

    def test_pe_embedded_pdb_and_legacy_compression_reject(self) -> None:
        data = bytearray(minimal_pe())
        struct.pack_into("<II", data, 0x98 + 112 + 6 * 8, 0x1200, 28)
        struct.pack_into("<I", data, 0x400 + 12, 17)
        with self.assertRaisesRegex(PackageError, "embedded compressed PDB"):
            _validate_payload("smoke.dll", bytes(data))
        data = bytearray(minimal_pe())
        data[0x188:0x190] = b".zdebug\0"
        with self.assertRaisesRegex(PackageError, "PE compressed debug"):
            _validate_payload("smoke.dll", bytes(data))
        # The usual COFF long-name spelling must reach the same rejection.
        data[0x188:0x190] = b"/4\0\0\0\0\0\0"
        struct.pack_into("<II", data, 0x80 + 12, len(data), 0)
        names = b".zdebug_info\0"
        data.extend(struct.pack("<I", 4 + len(names)) + names)
        with self.assertRaisesRegex(PackageError, "PE compressed debug"):
            _validate_payload("smoke.dll", bytes(data))

    def test_macho_compression_rejects_in_thin_and_universal_slices(self) -> None:
        thin = compressed_macho()
        variants = [thin]
        for wide in (False, True):
            header = struct.pack(">II", 0xcafebabf if wide else 0xcafebabe, 1)
            entry = struct.pack(">IIQQII" if wide else ">IIIII", 0x1000007, 3, 64, len(thin), *([0, 0] if wide else [0]))
            variants.append((header + entry).ljust(64, b"\0") + thin)
        for data in variants:
            with self.subTest(magic=data[:4]):
                with self.assertRaisesRegex(PackageError, "Mach-O compressed debug"):
                    _validate_payload("smoke.dylib", data)

    @unittest.skipUnless(shutil.which("cargo") and shutil.which("git"), "Cargo and Git required")
    def test_real_package_rejects_unicode_controls(self) -> None:
        # Exercise actual audit, Cargo notices, Git metadata and package creation
        # with libraries that pass the native audit. Compressed debug sections
        # are covered through the payload scanner tests above.
        repo = Path(__file__).resolve().parents[1]
        marker = "q:/\u4e2d\u6587/private/source.rs\0".encode()
        controls = [("windows-x86_64", "control.dll", bytes(pe_binary()) + b"\0" + marker),
                    ("linux-x86_64", "control.so", bytes(elf_binary()) + b"\0" + marker)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target, name, data in controls:
                with self.subTest(target=target):
                    binary = root / name
                    binary.write_bytes(data)
                    with self.assertRaisesRegex(PackageError, "private absolute path"):
                        create_package(repo, target, binary, binary, root / target)
                    self.assertFalse((root / target).exists())


if __name__ == "__main__":
    unittest.main()
