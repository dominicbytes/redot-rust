# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from tests.binary_fixtures import elf_binary, fat_binary, macho_slice, pe_binary
from tools.verify_binary_deps import (
    _elf_details,
    _pe_details,
    audit_binary,
    is_debug_runtime,
    main,
)

ARCHITECTURES = {
    "windows-x86_64": {"x86_64"},
    "linux-x86_64": {"x86_64"},
    "macos-universal": {"arm64", "x86_64"},
}


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


class ConsumerAuditTests(unittest.TestCase):
    def audit(self, data, target="windows-x86_64", **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(data)
            return audit_binary(
                path,
                expected_architectures=ARCHITECTURES[target],
                expected_target=target,
                **options,
            )

    def test_float_math_ucrt_api_set_is_a_system_dependency(self) -> None:
        # Import set of a Rapier physics DLL built with the pinned toolchain.
        for name in (
            "api-ms-win-core-synch-l1-2-0.dll", "api-ms-win-crt-heap-l1-1-0.dll",
            "api-ms-win-crt-math-l1-1-0.dll", "api-ms-win-crt-runtime-l1-1-0.dll",
            "api-ms-win-crt-string-l1-1-0.dll", "bcryptprimitives.dll",
            "KERNEL32.dll", "ntdll.dll", "VCRUNTIME140.dll",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.audit(pe_binary(name))["status"], "PASS")

    def test_ucrt_api_sets_outside_rust_std_stay_rejected(self) -> None:
        for name in (
            "api-ms-win-crt-stdio-l1-1-0.dll", "api-ms-win-crt-convert-l1-1-0.dll",
            "api-ms-win-crt-locale-l1-1-0.dll", "api-ms-win-crt-private-l1-1-0.dll",
            "ucrtbase.dll",
        ):
            with self.subTest(name=name):
                report = self.audit(pe_binary(name))
                self.assertEqual(report["status"], "FAIL")
                self.assertIn("non-system dependency is not bundled by this package", report["failures"])

    def test_extra_exports_fail_only_in_exact_mode(self) -> None:
        for data, target in [
            (pe_binary("KERNEL32.dll", exports=("gdext_rust_init", "init")), "windows-x86_64"),
            (elf_binary(exports=("gdext_rust_init", "init")), "linux-x86_64"),
            (fat_binary(
                first=macho_slice(0x01000007, exports=("gdext_rust_init", "init")),
                second=macho_slice(0x0100000C, exports=("gdext_rust_init", "init")),
            ), "macos-universal"),
        ]:
            with self.subTest(target=target):
                lenient = self.audit(data, target)
                self.assertEqual(lenient["status"], "PASS", lenient["failures"])
                self.assertEqual(lenient["unexpected_exports"], ["init"])
                strict = self.audit(data, target, exact_exports=True)
                self.assertEqual(strict["status"], "FAIL")
                self.assertTrue(strict["exact_exports"])
                self.assertEqual(strict["unexpected_export_count"], 1)
                self.assertEqual(strict["unexpected_exports"], ["init"])
                self.assertTrue(all("unexpected exports in" in failure for failure in strict["failures"]))

    def test_exact_mode_passes_a_single_entry_symbol_on_every_platform(self) -> None:
        for data, target in [
            (pe_binary("KERNEL32.dll"), "windows-x86_64"),
            (elf_binary(), "linux-x86_64"),
            (fat_binary(), "macos-universal"),
            (fat_binary(
                first=macho_slice(0x01000007, trie=True),
                second=macho_slice(0x0100000C, trie=True),
            ), "macos-universal"),
        ]:
            with self.subTest(target=target):
                report = self.audit(data, target, exact_exports=True)
                self.assertEqual(report["status"], "PASS", report["failures"])
                self.assertEqual(report["unexpected_export_count"], 0)

    def test_exact_mode_checks_each_universal_slice(self) -> None:
        for trie in (False, True):
            with self.subTest(trie=trie):
                data = fat_binary(
                    first=macho_slice(0x01000007, trie=trie),
                    second=macho_slice(0x0100000C, trie=trie, exports=("gdext_rust_init", "init")),
                )
                report = self.audit(data, "macos-universal", exact_exports=True)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["failures"], ["unexpected exports in arm64: 1"])
                self.assertEqual([row["unexpected_export_count"] for row in report["slices"]], [0, 1])
                self.assertEqual([row["exports"] for row in report["slices"]], [["gdext_rust_init"]] * 2)

    def test_gdext_hot_reload_hook_is_permitted_only_on_linux(self) -> None:
        exports = ("gdext_rust_init", "__cxa_thread_atexit_impl")
        linux = self.audit(elf_binary(exports=exports), "linux-x86_64", exact_exports=True)
        self.assertEqual(linux["status"], "PASS", linux["failures"])
        windows = self.audit(pe_binary("KERNEL32.dll", exports=exports), exact_exports=True)
        self.assertEqual(windows["failures"], ["unexpected exports in x86_64: 1"])

    def test_unexpected_export_names_are_listed_only_when_short_identifiers(self) -> None:
        names = ("gdext_rust_init", "init", "x" * 65, "_ZN4core3fmt5write17h0123456789abcdefE.llvm.1")
        report = self.audit(elf_binary(exports=names), "linux-x86_64", exact_exports=True)
        self.assertEqual(report["unexpected_export_count"], 3)
        self.assertEqual(report["unexpected_exports"], ["init"])
        self.assertEqual(report["exports"], ["gdext_rust_init"])
        self.assertNotIn("x" * 65, json.dumps(report))

    def test_consumer_entry_symbol_through_the_command_line(self) -> None:
        def run(exports, *extra):
            with tempfile.TemporaryDirectory() as directory:
                binary = Path(directory) / "godot_rapier.dll"
                binary.write_bytes(pe_binary("api-ms-win-crt-math-l1-1-0.dll", exports=exports))
                report_path = Path(directory) / "report.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main([
                        str(binary), "--target", "windows-x86_64", "--architecture", "x86_64",
                        "--required-export", "rapier_2d_init", "--report", str(report_path), *extra,
                    ])
                return code, json.loads(report_path.read_text(encoding="utf-8"))

        code, report = run(("rapier_2d_init",), "--exact-exports")
        self.assertEqual((code, report["status"]), (0, "PASS"))
        self.assertEqual(report["exports"], ["rapier_2d_init"])
        code, report = run(("init", "rapier_2d_init"), "--exact-exports")
        self.assertEqual((code, report["failures"]), (2, ["unexpected exports in x86_64: 1"]))
        self.assertEqual(report["unexpected_exports"], ["init"])
        self.assertEqual(run(("init", "rapier_2d_init"))[0], 0)
        code, report = run(("gdext_rust_init",))
        self.assertEqual((code, report["failures"]), (2, ["required export is missing in x86_64: rapier_2d_init"]))


if __name__ == "__main__":
    unittest.main()
