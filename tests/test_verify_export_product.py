# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.binary_fixtures import elf_binary, fat_binary, macho_slice, pe_binary
from tools.package_release import TARGETS
from tools.verify_export_product import ExportProductError, archive_library, verify_export


class VerifyExportProductTests(unittest.TestCase):
    def test_rejects_wrong_format_linux_export_with_matching_source_hash(self) -> None:
        slice_data = macho_slice(0x01000007)
        macho = struct.pack(">7I", 0xCAFEBABE, 1, 0x01000007, 0, 28, len(slice_data), 0)
        for data in (pe_binary(), macho + slice_data):
            with self.subTest(magic=data[:4]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                product = root / "fixture.x86_64"
                product.write_bytes(b"product")
                library = root / TARGETS["linux-x86_64"]["debug_name"]
                library.write_bytes(data)
                report = verify_export(
                    product, "linux-x86_64", "debug", expected_library=library
                )
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(
                    report["failures"], ["binary format does not match target"]
                )

    def test_rejects_elf_in_macos_export(self) -> None:
        expected = TARGETS["macos-universal"]["debug_name"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / expected
            library.write_bytes(elf_binary())
            product = root / "fixture.zip"
            with zipfile.ZipFile(product, "w") as package:
                package.writestr(
                    f"Fixture.app/Contents/Frameworks/{expected}", library.read_bytes()
                )
            report = verify_export(
                product, "macos-universal", "debug", expected_library=library
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("binary format does not match target", report["failures"])

    def test_accepts_matching_target_formats(self) -> None:
        for target, data in (
            ("windows-x86_64", pe_binary()),
            ("linux-x86_64", elf_binary()),
            ("macos-universal", fat_binary()),
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                library = root / TARGETS[target]["debug_name"]
                library.write_bytes(data)
                product = root / "fixture.zip"
                if target == "macos-universal":
                    with zipfile.ZipFile(product, "w") as package:
                        package.writestr(
                            f"Fixture.app/Contents/Frameworks/{library.name}", data
                        )
                else:
                    product.write_bytes(b"product")
                report = verify_export(product, target, "debug", expected_library=library)
                self.assertEqual(report["status"], "PASS", report["failures"])
                self.assertEqual(report["binary_audit"]["expected_target"], target)

    def test_selects_the_only_profile_library_from_macos_bundle(self) -> None:
        expected = "libredot_rust_smoke.macos.template_debug.universal.dylib"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            path = f"Fixture.app/Contents/Frameworks/{expected}"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(path, b"binary")
            member, data, native = archive_library(archive, expected)
            self.assertEqual(member, path)
            self.assertEqual(data, b"binary")
            self.assertEqual(native, [path])

    def test_rejects_wrong_profile_library_in_macos_bundle(self) -> None:
        expected = "libredot_rust_smoke.macos.template_debug.universal.dylib"
        release = "libredot_rust_smoke.macos.template_release.universal.dylib"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(f"Fixture.app/Contents/Frameworks/{expected}", b"d")
                package.writestr(f"Fixture.app/Contents/Frameworks/{release}", b"r")
            with self.assertRaisesRegex(ExportProductError, "unexpected native"):
                archive_library(archive, expected)

    def test_rejects_archive_traversal(self) -> None:
        expected = "libredot_rust_smoke.macos.template_debug.universal.dylib"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape", b"bad")
                package.writestr(f"Fixture.app/Contents/Frameworks/{expected}", b"d")
            with self.assertRaisesRegex(ExportProductError, "unsafe archive path"):
                archive_library(archive, expected)


if __name__ == "__main__":
    unittest.main()
