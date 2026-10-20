# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import struct
import tempfile
import unittest
from pathlib import Path

from tests.binary_fixtures import elf_binary, fat_binary, macho_slice, pe_binary
from tools.verify_binary_deps import BinaryAuditError, audit_binary


class BinarySecurityTests(unittest.TestCase):
    def audit(self, data, target="windows-x86_64"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(data)
            architectures = {"x86_64", "arm64"} if target == "macos-universal" else {"x86_64"}
            return audit_binary(
                path, expected_architectures=architectures, expected_target=target
            )

    def assert_failed(self, data, message, target="windows-x86_64"):
        report = self.audit(data, target)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any(message in failure for failure in report["failures"]), report["failures"])
        return report

    def test_all_three_formats_and_shared_types_pass(self):
        for data, target in [
            (pe_binary("KERNEL32.dll"), "windows-x86_64"),
            (elf_binary(), "linux-x86_64"),
            (fat_binary(), "macos-universal"),
        ]:
            with self.subTest(target=target):
                self.assertEqual(self.audit(data, target)["status"], "PASS")

    def test_rejects_wrong_format_for_target(self):
        self.assert_failed(pe_binary(), "format", "linux-x86_64")
        self.assert_failed(elf_binary(), "format", "windows-x86_64")

    def test_rejects_executables_with_matching_architecture_and_export(self):
        for data, target in [
            (pe_binary(dll=False), "windows-x86_64"),
            (elf_binary(shared=False), "linux-x86_64"),
            (fat_binary(second=macho_slice(0x0100000C, filetype=2)), "macos-universal"),
        ]:
            with self.subTest(target=target):
                self.assert_failed(data, "shared library", target)

    def test_requires_entrypoint_in_each_macho_slice_and_preserves_slice_data(self):
        report = self.assert_failed(
            fat_binary(second=macho_slice(0x0100000C, export=False)),
            "arm64", "macos-universal",
        )
        self.assertEqual(report["slices"][0]["exports"], ["gdext_rust_init"])
        self.assertEqual(report["slices"][1]["exports"], [])
        self.assertEqual(report["slices"][1]["architecture"], "arm64")

    def test_rejects_relabeled_macho_cpu(self):
        with self.assertRaisesRegex(BinaryAuditError, "CPU label"):
            self.audit(fat_binary(second=macho_slice(0x01000007)), "macos-universal")

    def test_macho_export_trie_entrypoint_is_checked_in_each_slice(self):
        data = fat_binary(
            first=macho_slice(0x01000007, trie=True),
            second=macho_slice(0x0100000C, trie=True),
        )
        self.assertEqual(self.audit(data, "macos-universal")["status"], "PASS")

    def test_fat64_slices_pass(self):
        self.assertEqual(self.audit(fat_binary(fat64=True), "macos-universal")["status"], "PASS")

    def test_rejects_invalid_fat_slice_layouts(self):
        for field, value, message in [
            (28, 0x01000007, "duplicate"), (36, 48, "overlapping"),
            (36, 16, "bounds"), (40, 100000, "bounds"), (44, 64, "alignment"),
        ]:
            with self.subTest(field=field, value=value):
                data = fat_binary()
                struct.pack_into(">I", data, field, value)
                with self.assertRaisesRegex(BinaryAuditError, message):
                    self.audit(data, "macos-universal")

    def test_macho_export_tables_cannot_escape_slice_into_next_slice(self):
        first = macho_slice(0x01000007, trie=True)
        struct.pack_into("<II", first, 40, len(first), 8)
        with self.assertRaisesRegex(BinaryAuditError, "slice bounds"):
            self.audit(fat_binary(first=first), "macos-universal")

    def test_macho_load_path_string_must_terminate_inside_command(self):
        first = macho_slice(0x01000007, dependency="/usr/lib/libSystem.B.dylib")
        command_size = struct.unpack_from("<I", first, 36)[0]
        first[32 + 24:32 + command_size] = b"x" * (command_size - 24)
        with self.assertRaisesRegex(BinaryAuditError, "unterminated"):
            self.audit(fat_binary(first=first), "macos-universal")

    def test_delay_imports_are_audited_in_both_address_modes(self):
        for va in (False, True):
            with self.subTest(va=va):
                report = self.assert_failed(pe_binary("VCRUNTIME140D.dll", delay=True, va=va), "debug runtime")
                self.assertEqual(report["delay_dependencies"], ["VCRUNTIME140D.dll"])
                self.assertIn("VCRUNTIME140D.dll", report["dependencies"])
                self.assertEqual(self.audit(pe_binary("KERNEL32.dll", delay=True, va=va))["status"], "PASS")

    def test_rejects_truncated_unterminated_and_invalid_delay_descriptors(self):
        for location, value, message in [
            (0x98 + 112 + 13 * 8 + 4, 32, "terminate"),
            (0x304, 0xFFFF, "bounds"), (0x300, 2, "attributes"),
        ]:
            with self.subTest(location=location):
                data = pe_binary("KERNEL32.dll", delay=True)
                struct.pack_into("<I", data, location, value)
                with self.assertRaisesRegex(BinaryAuditError, message):
                    self.audit(data)

    def test_rejects_unbundled_dependencies_on_every_platform(self):
        missing_slice = macho_slice(0x0100000C, dependency="@rpath/libmissing.dylib")
        for data, target in [
            (pe_binary("missing_control.dll"), "windows-x86_64"),
            (pe_binary("missing_control.dll", delay=True), "windows-x86_64"),
            (elf_binary("libmissing_control.so"), "linux-x86_64"),
            (fat_binary(second=missing_slice), "macos-universal"),
        ]:
            with self.subTest(target=target):
                self.assert_failed(data, "not bundled", target)

    def test_known_smoke_system_dependencies_pass(self):
        from tools.verify_binary_deps import SYSTEM_DEPENDENCIES
        for platform, names in SYSTEM_DEPENDENCIES.items():
            for name in names:
                if platform == "windows":
                    data, target = pe_binary(name), "windows-x86_64"
                elif platform == "linux":
                    data, target = elf_binary(name), "linux-x86_64"
                else:
                    data, target = fat_binary(first=macho_slice(0x01000007, dependency=name)), "macos-universal"
                with self.subTest(name=name):
                    self.assertEqual(self.audit(data, target)["status"], "PASS")

    def test_rejects_absolute_dependency_load_paths(self):
        host_slice = macho_slice(0x0100000C, dependency="/fictional/build/libmissing.dylib")
        for data, target in [
            (pe_binary("Z:/fictional/build/KERNEL32.dll"), "windows-x86_64"),
            (elf_binary("/fictional/build/libc.so.6"), "linux-x86_64"),
            (fat_binary(second=host_slice), "macos-universal"),
        ]:
            with self.subTest(target=target):
                self.assert_failed(data, "not bundled", target)

    def test_rejects_host_specific_rpath_and_runpath(self):
        for path in ("/fictional/build/root", "$ORIGIN:/fictional/build/root", "relative/path", "$ORIGIN/../outside", ""):
            with self.subTest(path=path):
                self.assert_failed(elf_binary(search_path=path), "search path", "linux-x86_64")
        self.assert_failed(fat_binary(second=macho_slice(0x0100000C, rpath="/fictional/build/root")), "search path", "macos-universal")

    def test_accepts_portable_loader_relative_search_paths(self):
        self.assertEqual(self.audit(elf_binary(search_path="$ORIGIN/lib"), "linux-x86_64")["status"], "PASS")
        self.assertEqual(self.audit(fat_binary(first=macho_slice(0x01000007, rpath="@loader_path/lib")), "macos-universal")["status"], "PASS")

    def test_rejects_absolute_dylib_install_name(self):
        self.assert_failed(fat_binary(first=macho_slice(0x01000007, install_name="/fictional/build/libsmoke.dylib")), "install name", "macos-universal")

    def test_incomplete_dependency_scans_cannot_pass(self):
        pe = pe_binary()
        struct.pack_into("<I", pe, 0x98 + 108, 2)
        self.assert_failed(pe, "incomplete")
        elf = elf_binary()
        struct.pack_into("<H", elf, 56, 1)
        self.assert_failed(elf, "incomplete", "linux-x86_64")

    def test_elf_filter_and_auxiliary_dependencies_are_audited(self):
        for tag in (0x7FFFFFFD, 0x7FFFFFFF):
            data = elf_binary("libmissing_control.so")
            struct.pack_into("<q", data, 0x200, tag)
            self.assert_failed(data, "not bundled", "linux-x86_64")

    def test_elf_dependencies_follow_loader_segment_without_dynamic_section(self):
        report = self.assert_failed(elf_binary("libmissing_control.so"), "not bundled", "linux-x86_64")
        self.assertEqual(report["dependencies"], ["libmissing_control.so"])


if __name__ == "__main__":
    unittest.main()
