import tempfile
import unittest
from pathlib import Path

from tools.materialize_fixture import MaterializeError, materialize_fixture


class MaterializeFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        (self.source / "bin").mkdir(parents=True)
        (self.source / "project.godot").write_text(
            '[compatibility]\nexpected_library_profile="debug"\n',
            encoding="utf-8",
        )
        (self.source / "bin" / "redot_rust_smoke.gdextension").write_text(
            '[libraries]\n'
            'windows.debug.x86_64 = "res://bin/debug.dll"\n'
            'windows.release.x86_64 = "res://bin/release.dll"\n'
            'linux.debug.x86_64 = "res://bin/debug.so"\n'
            'linux.release.x86_64 = "res://bin/release.so"\n',
            encoding="utf-8",
        )
        for name in (
            "redot_rust_smoke.windows.template_debug.x86_64.dll",
            "redot_rust_smoke.windows.template_release.x86_64.dll",
            "libredot_rust_smoke.linux.template_debug.x86_64.so",
            "libredot_rust_smoke.linux.template_release.x86_64.so",
        ):
            (self.source / "bin" / name).write_bytes(name.encode("ascii"))
        (self.source / ".godot").mkdir()
        (self.source / ".results").mkdir()

    def test_release_profile_rewrites_both_engine_mappings(self) -> None:
        output = self.root / "release fixture"
        result = materialize_fixture(self.source, output, "release")

        extension = (
            output / "bin" / "redot_rust_smoke.gdextension"
        ).read_text(encoding="utf-8")
        self.assertEqual(extension.count(result["binary"]), 2)
        self.assertIn('expected_library_profile="release"', (output / "project.godot").read_text(encoding="utf-8"))
        self.assertFalse((output / ".godot").exists())
        self.assertFalse((output / ".results").exists())
        self.assertTrue((output / "bin" / "profile-selection.json").is_file())

    def test_linux_profile_rewrites_only_linux_mappings(self) -> None:
        output = self.root / "linux fixture"
        result = materialize_fixture(
            self.source, output, "debug", target_name="linux-x86_64"
        )

        extension = (
            output / "bin" / "redot_rust_smoke.gdextension"
        ).read_text(encoding="utf-8")
        self.assertEqual(extension.count(result["binary"]), 2)
        self.assertIn('windows.debug.x86_64 = "res://bin/debug.dll"', extension)

    def test_existing_output_is_rejected(self) -> None:
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(MaterializeError, "output already exists"):
            materialize_fixture(self.source, output, "debug")

    def test_output_inside_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(MaterializeError, "outside the source"):
            materialize_fixture(self.source, self.source / "copy", "debug")


if __name__ == "__main__":
    unittest.main()
