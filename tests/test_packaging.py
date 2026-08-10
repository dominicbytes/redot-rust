# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.install_package import InstallError, inspect_package, install_package
from tools.package_release import MANIFEST_NAME, create_package


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
            root = Path(directory)
            repo = self._repo(root)
            debug = root / "debug.dll"
            release = root / "release.dll"
            debug.write_bytes(b"debug")
            release.write_bytes(b"release")
            first = root / "first"
            second = root / "second"
            with patch("tools.package_release.audit_binary", side_effect=self._audit):
                one = create_package(repo, "windows-x86_64", debug, release, first)
                two = create_package(repo, "windows-x86_64", debug, release, second)
            self.assertEqual(one["archive_sha256"], two["archive_sha256"])
            package = first / one["archive"]
            project = root / "project"
            project.mkdir()
            (project / "project.godot").write_text("config_version=5\n", encoding="utf-8")
            installed = install_package(package, project, expected_target="windows-x86_64")
            self.assertEqual(installed["status"], "PASS")
            self.assertTrue(project.joinpath(MANIFEST_NAME).is_file())
            with self.assertRaisesRegex(InstallError, "overwrite"):
                install_package(package, project)

    def test_rejects_traversal_before_manifest_processing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../escape", b"bad")
            with self.assertRaisesRegex(InstallError, "unsafe archive path"):
                inspect_package(package)


if __name__ == "__main__":
    unittest.main()
