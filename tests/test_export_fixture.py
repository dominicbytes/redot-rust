# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tools.export_fixture import ExportError, export_preset_text


class ExportFixtureTests(unittest.TestCase):
    def test_generates_profile_specific_linux_preset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "linux_release.x86_64"
            template.write_bytes(b"template")
            text = export_preset_text("linux-x86_64", "release", template)
            self.assertIn('platform="Linux"', text)
            self.assertIn('custom_template/debug=""', text)
            self.assertIn(template.resolve().as_posix(), text)
            self.assertIn("binary_format/embed_pck=true", text)

    def test_supports_container_template_reference(self) -> None:
        template = Path("linux_debug.x86_64")
        text = export_preset_text(
            "linux-x86_64",
            "debug",
            template,
            template_reference="/templates/linux_debug.x86_64",
        )
        self.assertIn('/templates/linux_debug.x86_64', text)

    def test_macos_preset_uses_both_template_slots_and_bundle_identifier(self) -> None:
        template = Path("macos.zip")
        text = export_preset_text("macos-universal", "debug", template)
        template_value = template.resolve().as_posix()
        self.assertEqual(text.count(template_value), 2)
        self.assertIn('binary_format/architecture="universal"', text)
        self.assertIn("texture_format/etc2_astc=true", text)
        self.assertIn('application/bundle_identifier="org.redotengine.redotrustcompat"', text)
        self.assertIn("codesign/codesign=0", text)

    def test_rejects_wrong_template_name(self) -> None:
        with self.assertRaisesRegex(ExportError, "requires"):
            export_preset_text(
                "windows-x86_64", "debug", Path("windows_release_x86_64.exe")
            )


if __name__ == "__main__":
    unittest.main()
