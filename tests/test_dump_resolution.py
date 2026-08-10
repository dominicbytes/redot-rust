import tempfile
import unittest
from pathlib import Path

from tools.dump_redot_api import DumpError, resolve_dump_editor


class DumpResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_console_launcher_resolves_to_sibling_editor(self) -> None:
        launcher = self.root / "redot.windows.editor.x86_64.console.exe"
        editor = self.root / "redot.windows.editor.x86_64.exe"
        launcher.touch()
        editor.touch()

        resolved = resolve_dump_editor(launcher)

        self.assertEqual(resolved.editor, editor.resolve())
        self.assertEqual(resolved.launcher, launcher.resolve())

    def test_console_launcher_without_editor_fails_before_execution(self) -> None:
        launcher = self.root / "redot.windows.editor.x86_64.console.exe"
        launcher.touch()

        with self.assertRaisesRegex(DumpError, "console launcher cannot run dump commands"):
            resolve_dump_editor(launcher)

    def test_actual_editor_is_accepted(self) -> None:
        editor = self.root / "redot.windows.editor.x86_64.exe"
        editor.touch()

        resolved = resolve_dump_editor(editor)

        self.assertEqual(resolved.editor, editor.resolve())
        self.assertIsNone(resolved.launcher)


if __name__ == "__main__":
    unittest.main()
