# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.prepare_clean_install import prepare_clean_install


class CleanInstallTests(unittest.TestCase):
    def test_prepares_new_project_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "project.godot").write_text(
                '[compatibility]\nexpected_library_profile="debug"\n', encoding="utf-8"
            )
            fake_install = {
                "schema_version": 1,
                "status": "PASS",
                "target": "linux-x86_64",
                "version": "test",
                "installed_files": [],
            }
            def fake_installer(_package: Path, project: Path, **_kwargs: object) -> dict:
                addon = project / "addons" / "redot-rust-compat"
                addon.mkdir(parents=True)
                (addon / "redot_rust_smoke.gdextension").write_text(
                    '[libraries]\n'
                    'linux.debug.x86_64 = "res://debug.so"\n'
                    'linux.release.x86_64 = "res://release.so"\n',
                    encoding="utf-8",
                )
                return fake_install

            with patch(
                "tools.prepare_clean_install.install_package",
                side_effect=fake_installer,
            ):
                report = prepare_clean_install(
                    source,
                    root / "output",
                    root / "package.zip",
                    "linux-x86_64",
                    "release",
                )
            self.assertEqual(report["status"], "PASS")
            text = (root / "output" / "project.godot").read_text(encoding="utf-8")
            self.assertIn('expected_library_profile="release"', text)
            extension_list = root / "output" / ".godot" / "extension_list.cfg"
            self.assertIn("addons/redot-rust-compat", extension_list.read_text(encoding="utf-8"))
            extension = (
                root
                / "output"
                / "addons"
                / "redot-rust-compat"
                / "redot_rust_smoke.gdextension"
            ).read_text(encoding="utf-8")
            self.assertEqual(
                extension.count(
                    "libredot_rust_smoke.linux.template_release.x86_64.so"
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
