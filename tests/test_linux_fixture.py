# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tools.run_linux_fixture import LinuxFixtureError, PROFILES, run_linux_fixture


class LinuxFixtureTests(unittest.TestCase):
    def test_profile_contract_names_exact_template_and_library(self) -> None:
        self.assertEqual(PROFILES["debug"]["template"], "linux_debug.x86_64")
        self.assertEqual(
            PROFILES["release"]["library"],
            "libredot_rust_smoke.linux.template_release.x86_64.so",
        )

    def test_rejects_wrong_template_before_materializing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "linux_release.x86_64"
            template.write_bytes(b"template")
            with self.assertRaisesRegex(LinuxFixtureError, "requires template"):
                run_linux_fixture(
                    root / "source",
                    root / "output",
                    "debug",
                    root / "redot.exe",
                    template,
                    root / "reports",
                )


if __name__ == "__main__":
    unittest.main()
