# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tools.build_macos_universal import DEFAULT_IMAGE, MacBuildError, docker_command


class BuildMacosUniversalTests(unittest.TestCase):
    def test_command_pins_image_toolchain_target_and_read_only_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = root / "api" / "extension_api.json"
            output = root / "output"
            command = docker_command(root, api, output, "release")
            text = " ".join(str(value) for value in command)
            self.assertIn(DEFAULT_IMAGE, command)
            self.assertIn("1.94.0", text)
            self.assertIn("universal2-apple-darwin", text)
            self.assertIn("--release", text)
            self.assertIn(":/workspace:ro", text)
            self.assertIn(":/api:ro", text)

    def test_rejects_unknown_profile(self) -> None:
        with self.assertRaisesRegex(MacBuildError, "unsupported profile"):
            docker_command(Path("."), Path("api.json"), Path("out"), "fast")


if __name__ == "__main__":
    unittest.main()
