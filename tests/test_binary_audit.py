# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import struct
import unittest

from tools.verify_binary_deps import _elf_details, _pe_details, is_debug_runtime


class BinaryAuditTests(unittest.TestCase):
    def test_reads_minimal_pe64_architecture(self) -> None:
        data = bytearray(0x200)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 0x3C, 0x80)
        data[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", data, 0x84, 0x8664, 0)
        struct.pack_into("<H", data, 0x94, 0xF0)
        struct.pack_into("<H", data, 0x98, 0x20B)
        report = _pe_details(bytes(data))
        self.assertEqual(report["architectures"], ["x86_64"])
        self.assertEqual(report["format"], "PE32+")

    def test_reads_minimal_elf64_architecture(self) -> None:
        data = bytearray(64)
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 18, 62)
        struct.pack_into("<Q", data, 40, 64)
        struct.pack_into("<HH", data, 58, 64, 0)
        report = _elf_details(bytes(data))
        self.assertEqual(report["architectures"], ["x86_64"])
        self.assertEqual(report["format"], "ELF64")

    def test_debug_runtime_detection_is_narrow(self) -> None:
        self.assertTrue(is_debug_runtime("VCRUNTIME140D.dll"))
        self.assertTrue(is_debug_runtime("ucrtbased.dll"))
        self.assertFalse(is_debug_runtime("VCRUNTIME140.dll"))
        self.assertFalse(is_debug_runtime("libc.so.6"))


if __name__ == "__main__":
    unittest.main()
