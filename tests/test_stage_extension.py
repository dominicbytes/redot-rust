import json
import tempfile
import unittest
from pathlib import Path

from tools.stage_extension import StageError, stage_target_binaries, stage_windows_binaries


class StageExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.target = self.root / "target"
        self.project = self.root / "project with spaces"

    def test_debug_and_release_binaries_use_manifest_names(self) -> None:
        debug = self.target / "debug" / "redot_rust_smoke.dll"
        release = self.target / "release" / "redot_rust_smoke.dll"
        debug.parent.mkdir(parents=True)
        release.parent.mkdir(parents=True)
        debug.write_bytes(b"debug")
        release.write_bytes(b"release")

        manifest = stage_windows_binaries(self.target, self.project)

        bin_dir = self.project / "bin"
        self.assertTrue(
            (bin_dir / "redot_rust_smoke.windows.template_debug.x86_64.dll").is_file()
        )
        self.assertTrue(
            (bin_dir / "redot_rust_smoke.windows.template_release.x86_64.dll").is_file()
        )
        written = json.loads((bin_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(written, manifest)
        self.assertEqual(sorted(manifest["profiles"]), ["debug", "release"])

    def test_missing_profile_fails_without_partial_staging(self) -> None:
        debug = self.target / "debug" / "redot_rust_smoke.dll"
        debug.parent.mkdir(parents=True)
        debug.write_bytes(b"debug")

        with self.assertRaisesRegex(StageError, "release binary does not exist"):
            stage_windows_binaries(self.target, self.project)

        self.assertFalse((self.project / "bin").exists())

    def test_explicit_linux_binaries_use_linux_manifest_names(self) -> None:
        debug = self.root / "debug.so"
        release = self.root / "release.so"
        debug.write_bytes(b"debug")
        release.write_bytes(b"release")

        manifest = stage_target_binaries(
            debug, release, self.project, "linux-x86_64"
        )

        self.assertEqual(manifest["target"], "linux-x86_64")
        self.assertTrue(
            (
                self.project
                / "bin"
                / "libredot_rust_smoke.linux.template_debug.x86_64.so"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
