# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import patch

from tests.binary_fixtures import fat_binary
from tools.build_macos_universal import (
    DEFAULT_IMAGE,
    MacBuildError,
    build_macos_universal,
    docker_command,
)


class BuildMacosUniversalTests(unittest.TestCase):
    def test_audits_built_library_for_macos_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            source = output / "target/universal2-apple-darwin/debug/libredot_rust_smoke.dylib"
            source.parent.mkdir(parents=True)
            source.write_bytes(fat_binary())
            with (
                patch("tools.build_macos_universal.load_lock"),
                patch("tools.build_macos_universal.inspect_api"),
                patch("tools.build_macos_universal.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                report = build_macos_universal(
                    root, root / "api.json", output, root / "lock.json", profiles=("debug",)
                )
            audit = report["binaries"]["debug"]
            self.assertEqual(audit["status"], "PASS", audit["failures"])
            self.assertEqual(audit["expected_target"], "macos-universal")

    def test_command_pins_image_toolchain_target_and_read_only_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "redot-api.lock.json").write_bytes((Path(__file__).parents[1] / "redot-api.lock.json").read_bytes())
            api = root / "api" / "extension_api.json"
            output = root / "output"
            command = docker_command(root, api, output, "release")
            text = " ".join(str(value) for value in command)
            self.assertIn(DEFAULT_IMAGE, command)
            self.assertIn("1.94.0", text)
            self.assertIn("universal2-apple-darwin", text)
            self.assertIn("--release", text)
            self.assertIn(":/workspace:ro", text)
            self.assertIn(f"{api.resolve()}:/api/extension_api.json:ro", command)

    def test_renamed_validated_api_is_the_only_mounted_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "redot-api.lock.json").write_bytes((Path(__file__).parents[1] / "redot-api.lock.json").read_bytes())
            api = root / "renamed-locked-api.json"
            api.write_text("locked")
            (root / "extension_api.json").write_text("unchecked sibling")
            with patch.dict("os.environ", {"CARGO_ENCODED_RUSTFLAGS": "--cfg\x1fcustom"}):
                command = docker_command(root, api, root / "output", "debug")
            self.assertIn(f"{api.resolve()}:/api/extension_api.json:ro", command)
            flags = next(value for value in command if value.startswith("CARGO_ENCODED_RUSTFLAGS="))
            self.assertTrue(flags.startswith("CARGO_ENCODED_RUSTFLAGS=--cfg\x1fcustom\x1f"))
            for source in ("/workspace", "/output/target", "/output/cargo-home", "/root", "/usr/local/rustup"):
                self.assertIn(f"--remap-path-prefix={source}=", flags)

    def test_rejects_unknown_profile(self) -> None:
        with self.assertRaisesRegex(MacBuildError, "unsupported profile"):
            docker_command(Path("."), Path("api.json"), Path("out"), "fast")


if __name__ == "__main__":
    unittest.main()
