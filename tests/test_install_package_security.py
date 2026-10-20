# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.install_package import InstallError, inspect_package, install_package
from tools.package_release import ADDON_ROOT, MANIFEST_NAME


class InstallerSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text("config_version=5\n")

    def archive(self, payload=None, *, compression=zipfile.ZIP_STORED):
        if payload is None:
            payload = [(str(ADDON_ROOT / "payload.txt"), b"verified")]
        lock = json.loads((Path(__file__).parents[1] / "redot-api.lock.json").read_text())
        manifest = {
            "binding_abi": lock["binding_abi"],
            "certified_engines": lock["certified_engines"],
            "coverage": lock["binding_abi"]["coverage"],
            "schema_version": 2,
            "name": "redot-rust-compat",
            "target": "windows-x86_64",
            "version": "test",
            "files": [
                {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest().upper()}
                for name, data in payload
            ],
        }
        path = self.root / "package.zip"
        with zipfile.ZipFile(path, "w", compression=compression) as package:
            for name, data in payload:
                package.writestr(name, data)
            package.writestr(MANIFEST_NAME, json.dumps(manifest))
        return path

    def assert_rejected_without_writes(self, package, message):
        with self.assertRaisesRegex(InstallError, message):
            install_package(package, self.project)
        self.assertFalse((self.project / "addons").exists())

    def test_rejects_unsafe_and_noncanonical_names_before_writes(self):
        prefix = str(ADDON_ROOT)
        names = [
            "Z:/outside.txt", "Z:outside.txt", "/outside.txt", "//host/share/file",
            "\\host\\share\\file", "addons/another-addon/file", "outside.txt",
            f"{prefix}/../file", f"{prefix}/./file", f"{prefix}//file", f"{prefix}/file/",
            f"{prefix}/file:stream", f"{prefix}/NUL.txt", f"{prefix}/CON .txt", f"{prefix}/com1/file",
            f"{prefix}/LPT\u00b9.txt", f"{prefix}/trailing.", f"{prefix}/trailing ",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assert_rejected_without_writes(self.archive([(name, b"bad")]), "unsafe")

    def test_rejects_case_aliases_on_windows_and_macos(self):
        payload = [
            (str(ADDON_ROOT / name), data)
            for name, data in [("A.txt", b"one"), ("a.txt", b"two")]
        ]
        package = self.archive(payload)
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), patch("tools.install_package.sys.platform", platform):
                self.assert_rejected_without_writes(package, "duplicate")

    def test_rejects_unicode_normalization_aliases_on_macos(self):
        package = self.archive([(str(ADDON_ROOT / name), b"content") for name in ("caf\u00e9.txt", "cafe\u0301.txt")])
        with patch("tools.install_package.sys.platform", "darwin"):
            self.assert_rejected_without_writes(package, "duplicate")

    def test_rejects_exact_duplicates(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            package = self.archive([(str(ADDON_ROOT / "a"), b"one")] * 2)
        self.assert_rejected_without_writes(package, "duplicate")

    def test_rejects_file_directory_aliases(self):
        package = self.archive([(str(ADDON_ROOT / "file"), b"one"), (str(ADDON_ROOT / "file/child"), b"two")])
        self.assert_rejected_without_writes(package, "conflicts")

    def test_checks_all_hashes_before_creating_directories(self):
        package = self.archive()
        with zipfile.ZipFile(package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries[str(ADDON_ROOT / "payload.txt")] = b"tampered"
        with zipfile.ZipFile(package, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        self.assert_rejected_without_writes(package, "hash mismatch")

    def test_rejects_zip_symlink(self):
        package = self.archive()
        with zipfile.ZipFile(package, "a") as archive:
            info = zipfile.ZipInfo(str(ADDON_ROOT / "link"))
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "target")
        self.assert_rejected_without_writes(package, "regular files")

    def test_bounds_inventory_before_reading_any_member(self):
        package = self.archive()
        for constant, limit, message in [
            ("MAX_MEMBERS", 1, "member count"),
            ("MAX_MANIFEST_SIZE", 1, "size limit"),
            ("MAX_FILE_SIZE", 1, "size limit"),
            ("MAX_TOTAL_SIZE", 1, "total uncompressed"),
        ]:
            with self.subTest(constant=constant), patch("tools.install_package." + constant, limit):
                with patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("read before bounds")):
                    self.assert_rejected_without_writes(package, message)

    def test_rejects_compression_bomb_before_reading(self):
        package = self.archive([(str(ADDON_ROOT / "zeros.bin"), b"0" * (32 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED)
        with patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("read before bounds")):
            self.assert_rejected_without_writes(package, "compression ratio")

    def test_rejects_codecs_with_external_memory_parameters_before_reading(self):
        package = self.archive(compression=zipfile.ZIP_LZMA)
        with patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("read before codec check")):
            self.assert_rejected_without_writes(package, "compression method")

    def test_hashes_and_extracts_in_bounded_chunks(self):
        package = self.archive([(str(ADDON_ROOT / "large.bin"), os.urandom(8192))])
        original = zipfile.ZipExtFile.read
        def bounded_read(source, size=-1):
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 1024 * 1024 + 1)
            return original(source, size)
        with (
            patch.object(zipfile.ZipExtFile, "read", bounded_read),
            patch.object(zipfile.ZipFile, "read", side_effect=AssertionError("unbounded read")),
        ):
            self.assertEqual(install_package(package, self.project)["status"], "PASS")

    def test_rejects_existing_directory_symlink(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.project / "addons"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlinks unavailable")
        with self.assertRaisesRegex(InstallError, "symlink or reparse"):
            install_package(self.archive(), self.project)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction control")
    def test_rejects_existing_junction_ancestors(self):
        outside = self.root / "outside"
        outside.mkdir()
        # Exercise both an add-on ancestor and the project directory itself.
        for link in (self.project / "addons", self.root / "project-link"):
            target = outside if link.name == "addons" else self.project
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
            )
            self.assertEqual(result.returncode, 0, "could not create scratch junction")
            try:
                destination = self.project if link.name == "addons" else link
                with self.assertRaisesRegex(InstallError, "symlink or reparse"):
                    install_package(self.archive(), destination)
                self.assertEqual(list(outside.iterdir()), [])
            finally:
                link.rmdir()

    def test_rejects_late_collision_before_creating_any_payload(self):
        package = self.archive([(str(ADDON_ROOT / "a/new.txt"), b"new"), (str(ADDON_ROOT / "z/existing.txt"), b"old")])
        existing = self.project / ADDON_ROOT / "z/existing.txt"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"keep")
        with self.assertRaisesRegex(InstallError, "overwrite"):
            install_package(package, self.project)
        self.assertFalse((self.project / ADDON_ROOT / "a").exists())
        self.assertEqual(existing.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
