# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.install_package import InstallError, inspect_package, install_package
from tools.package_release import MANIFEST_NAME, PackageError, _validate_payload, create_package


def minimal_pe() -> bytes:
    data = bytearray(0x1000)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 1)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x96, 0x2002)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    struct.pack_into("<IIII", data, 0x188 + 8, 0xE00, 0x1000, 0xE00, 0x200)
    struct.pack_into("<II", data, 0x98 + 112, 0x1000, 40)
    struct.pack_into("<I", data, 0x200 + 24, 1)
    struct.pack_into("<I", data, 0x200 + 32, 0x1040)
    struct.pack_into("<I", data, 0x240, 0x1080)
    data[0x280:0x290] = b"gdext_rust_init\0\0"
    return bytes(data)


class PackagingTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / "docs").mkdir(parents=True)
        for name in ("LICENSE", "NOTICE.md", "README.md", "UPSTREAM_LOCK.md"):
            (repo / name).write_text(name + "\n", encoding="utf-8")
        (repo / "docs" / "compatibility.md").write_text("compat\n", encoding="utf-8")
        (repo / "docs" / "limitations.md").write_text("limits\n", encoding="utf-8")
        (repo / "docs" / "package-readme.md").write_text(
            "package\n", encoding="utf-8"
        )
        (repo / "redot-api.lock.json").write_text(
            json.dumps(
                {
                    "redot": {
                        "version_string": "26.2.stable.official.test",
                        "engine_commit": "engine",
                        "precision": "single",
                    },
                    "godot_api": {
                        "version_major": 4,
                        "version_minor": 5,
                        "version_patch": 2,
                    },
                    "files": {"extension_api": {"sha256": "API"}},
                    "gdext": {"revision": "GDEXT", "crate_version": "0.5.5"},
                }
            ),
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _audit(binary: Path, **_kwargs: object) -> dict[str, object]:
        return {
            "status": "PASS",
            "sha256": "D" * 64 if "debug" in binary.name else "E" * 64,
            "architectures": ["x86_64"],
            "dependencies": ["KERNEL32.dll"],
        }

    def test_package_is_deterministic_and_installs_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = self._repo(root)
            debug = root / "debug.dll"
            release = root / "release.dll"
            debug.write_bytes(b"debug")
            release.write_bytes(b"release")
            first = root / "first"
            second = root / "second"
            with patch("tools.package_release.audit_binary", side_effect=self._audit), \
                    patch("tools.package_release.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "a" * 40)), \
                    patch("tools.package_release.third_party_bundle", return_value={
                        "dependency-inventory.json": b'{"linked_packages": []}',
                        "third-party/example-1.0/LICENSE": b"example notice",
                    }):
                one = create_package(repo, "windows-x86_64", debug, release, first)
                two = create_package(repo, "windows-x86_64", debug, release, second)
            self.assertEqual(one["archive_sha256"], two["archive_sha256"])
            with zipfile.ZipFile(first / one["archive"]) as archive:
                self.assertIn("addons/redot-rust-compat/third-party/example-1.0/LICENSE", archive.namelist())
                source = json.loads(archive.read("addons/redot-rust-compat/SOURCE.json"))
                self.assertEqual(source["revision"], "a" * 40)
                self.assertEqual(source["repository"], "https://github.com/dominicbytes/redot-rust")
                self.assertEqual(one["manifest"]["source"], source)
            package = first / one["archive"]
            project = root / "project"
            project.mkdir()
            (project / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            installed = install_package(package, project, expected_target="windows-x86_64")
            self.assertEqual(installed["status"], "PASS")
            self.assertTrue(project.joinpath(MANIFEST_NAME).is_file())
            with self.assertRaisesRegex(InstallError, "overwrite"):
                install_package(package, project)

    def test_alternate_lock_controls_packaged_identity_and_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            lock = json.loads((repo / "redot-api.lock.json").read_text())
            lock["redot"]["version_string"] = "26.3.stable.official.test"
            lock["godot_api"]["version_minor"] = 6
            alternate = repo / "alternate.lock.json"
            alternate.write_text(json.dumps(lock))
            binary = root / "debug.dll"
            binary.write_bytes(b"remapped binary")
            with patch("tools.package_release.audit_binary", side_effect=self._audit), \
                    patch("tools.package_release.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "a" * 40)), \
                    patch("tools.package_release.third_party_bundle", return_value={}):
                result = create_package(repo, "windows-x86_64", binary, binary, root / "output", lock_path=alternate)
            self.assertEqual(result["manifest"]["redot"]["version"], lock["redot"]["version_string"])
            with zipfile.ZipFile(root / "output" / result["archive"]) as archive:
                self.assertEqual(json.loads(archive.read("addons/redot-rust-compat/redot-api.lock.json")), lock)
                descriptor = archive.read("addons/redot-rust-compat/redot_rust_smoke.gdextension").decode()
                self.assertIn('compatibility_minimum = "4.6"', descriptor)

    def test_all_payloads_reject_private_paths_in_ascii_and_utf16(self) -> None:
        markers = [r"Q:\fictional\checkout\src.rs", "Q:/fictional/cache/src.rs",
                   "/home/fictitious/cache/src.rs", "/Users/fictitious/src.rs",
                   "/tmp/fictitious/build.rs", "/var/folders/fictitious/build.rs",
                   "/private/tmp/fictitious/src.rs", "/__w/example/example/src.rs",
                   "/github/workspace/src.rs", "/opt/hostedtoolcache/rust/src.rs",
                   r"\\fictional-host\share\src.rs"]
        for name in ("bin/smoke.dll", "bin/smoke.so", "bin/smoke.dylib", "LICENSE", "metadata.txt"):
            for marker in markers:
                for encoding in ("ascii", "utf-16le"):
                    with self.subTest(name=name, marker=marker, encoding=encoding):
                        data = b"\x01" + marker.encode(encoding) + b"\x00"
                        if marker.startswith("\\\\") and name.endswith((".so", ".dylib")):
                            # UNC shares only exist on Windows build hosts.
                            _validate_payload(name, data)
                            continue
                        with self.assertRaisesRegex(PackageError, "private absolute path") as caught:
                            _validate_payload(name, data)
                        self.assertNotIn(marker, str(caught.exception))
        with self.assertRaises(PackageError):
            _validate_payload("metadata.json", json.dumps({"path": markers[0]}).encode())
        _validate_payload("bin/smoke.dll", b"/rustc/0123456789/library/core/src/lib.rs\0__source/src/lib.rs")
        # A short drive-like instruction fragment is not a printable path.
        _validate_payload("bin/smoke.dll", bytes((0xFF, 0x43, 0x3A, 0x5C, 0x35, 0)))

    def test_short_nul_delimited_binary_paths_are_rejected(self) -> None:
        for encoding in ("ascii", "utf-16le"):
            with self.subTest(encoding=encoding):
                data = "\0Q:/x\0".encode(encoding)
                with self.assertRaisesRegex(PackageError, "private absolute path"):
                    _validate_payload("bin/smoke.dll", data)

    def test_binary_path_marker_stops_real_package_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            binary = root / "smoke.dll"
            binary.write_bytes(minimal_pe() + b"\0Q:/fictional/build/src.rs\0")
            with patch("tools.package_release.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "a" * 40)), \
                    patch("tools.package_release.third_party_bundle", return_value={}):
                with self.assertRaisesRegex(PackageError, "private absolute path"):
                    create_package(repo, "windows-x86_64", binary, binary, root / "output")
            self.assertFalse((root / "output").exists())

    def test_rejects_traversal_before_manifest_processing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../escape", b"bad")
            with self.assertRaisesRegex(InstallError, "unsafe archive path"):
                inspect_package(package)


if __name__ == "__main__":
    unittest.main()
