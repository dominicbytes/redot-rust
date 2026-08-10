import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.configure_cargo import prepare_environment
from tools.redot_api import IdentityError


FIXTURE = Path(__file__).parent / "api-fixtures" / "valid_api.json"


class ConfigureCargoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="redot compat ")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.api_path = self.root / "API files" / "extension_api.json"
        self.api_path.parent.mkdir()
        self.api_path.write_bytes(FIXTURE.read_bytes())
        digest = hashlib.sha256(self.api_path.read_bytes()).hexdigest().upper()
        lock = {
            "schema_version": 1,
            "redot": {
                "version_major": 26,
                "version_minor": 2,
                "version_patch": 0,
                "version_status": "stable",
                "version_build": "official",
                "precision": "single",
            },
            "godot_api": {
                "version_major": 4,
                "version_minor": 5,
                "version_patch": 2,
                "version_status": "stable",
                "version_build": "redot.official",
                "precision": "single",
                "supported_minor_min": 2,
                "supported_minor_max": 7,
            },
            "target": {"precision": "single", "build": "editor"},
            "files": {
                "extension_api": {"path": str(self.api_path), "sha256": digest},
                "gdextension_interface_json": {"available": False},
            },
        }
        self.lock_path = self.root / "redot-api.lock.json"
        self.lock_path.write_text(json.dumps(lock), encoding="utf-8")

    def test_adapter_supports_spaces_and_sets_only_required_gdext_variable(self) -> None:
        base = {
            "PATH": "unchanged",
            "GODOT4_GDEXTENSION_JSON": "stale",
            "GDRUST_GODOT_BIN": "stale",
            "GDRUST_GODOT_INTERFACE_JSON": "stale",
        }

        env, report = prepare_environment(self.lock_path, self.api_path, None, base)

        self.assertEqual(env["PATH"], "unchanged")
        self.assertEqual(env["GDRUST_GODOT_API_JSON"], str(self.api_path.resolve()))
        gdext_keys = sorted(key for key in env if key.startswith("GDRUST_") or key.startswith("GODOT4_"))
        self.assertEqual(gdext_keys, ["GDRUST_GODOT_API_JSON"])
        self.assertEqual(report["godot_api"], "4.5.2")
        self.assertEqual(report["redot_product"], "26.2.0")

    def test_optional_interface_json_is_set_only_when_requested(self) -> None:
        interface = self.root / "API files" / "gdextension_interface.json"
        interface.write_text("{}", encoding="utf-8")

        env, _ = prepare_environment(self.lock_path, self.api_path, interface, {})

        self.assertEqual(env["GDRUST_GODOT_INTERFACE_JSON"], str(interface.resolve()))

    def test_stale_api_hash_stops_before_cargo_environment_is_returned(self) -> None:
        self.api_path.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(IdentityError, "lock is stale"):
            prepare_environment(self.lock_path, self.api_path, None, {})


if __name__ == "__main__":
    unittest.main()
