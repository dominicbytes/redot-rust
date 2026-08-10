# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.verify_export_product import ExportProductError, archive_library


class VerifyExportProductTests(unittest.TestCase):
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
