import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.redot_api import IdentityError, inspect_api


FIXTURE = Path(__file__).parent / "api-fixtures" / "valid_api.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def make_lock(api_path: Path) -> dict:
    return {
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
        "target": {
            "platform": "windows",
            "architecture": "x86_64",
            "precision": "single",
            "build": "editor",
        },
        "files": {
            "extension_api": {
                "path": str(api_path),
                "sha256": sha256(api_path),
            },
            "gdextension_interface_json": {"available": False},
        },
    }


class ApiIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.api_path = Path(self.temp_dir.name) / "extension_api.json"
        self.api_path.write_bytes(FIXTURE.read_bytes())

    def load_api(self) -> dict:
        return json.loads(self.api_path.read_text(encoding="utf-8"))

    def write_api(self, value: dict) -> None:
        self.api_path.write_text(json.dumps(value), encoding="utf-8")

    def assert_identity_error(self, expected: str, lock: dict) -> None:
        with self.assertRaisesRegex(IdentityError, expected):
            inspect_api(self.api_path, lock)

    def test_valid_api_passes_and_keeps_both_identities(self) -> None:
        report = inspect_api(self.api_path, make_lock(self.api_path))
        self.assertEqual(report["redot_product"], "26.2.0")
        self.assertEqual(report["godot_api"], "4.5.2")
        self.assertEqual(report["precision"], "single")

    def test_missing_standard_header_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        del api["header"]
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("header: required standard Godot API header is missing", lock)

    def test_api_major_26_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        api["header"]["version_major"] = 26
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("header.version_major: expected 4, found 26", lock)

    def test_wrong_api_minor_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        api["header"]["version_minor"] = 6
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("header.version_minor: lock expects 5, found 6", lock)

    def test_unsupported_api_minor_fails(self) -> None:
        lock = make_lock(self.api_path)
        lock["godot_api"]["version_minor"] = 8
        api = self.load_api()
        api["header"]["version_minor"] = 8
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("header.version_minor: supported range is 2..7, found 8", lock)

    def test_precision_mismatch_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        api["header"]["precision"] = "double"
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("header.precision: target expects single, found double", lock)

    def test_build_metadata_mismatch_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        api["header"]["version_build"] = "official"
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error(
            "header.version_build: lock expects redot.official, found official", lock
        )

    def test_stale_hash_fails_before_identity_is_accepted(self) -> None:
        lock = make_lock(self.api_path)
        lock["files"]["extension_api"]["sha256"] = "0" * 64
        self.assert_identity_error("files.extension_api.sha256: lock is stale", lock)

    def test_redot_header_mismatch_fails(self) -> None:
        lock = make_lock(self.api_path)
        api = self.load_api()
        api["redot_header"]["version_minor"] = 3
        self.write_api(api)
        lock["files"]["extension_api"]["sha256"] = sha256(self.api_path)
        self.assert_identity_error("redot_header.version_minor: lock expects 2, found 3", lock)


if __name__ == "__main__":
    unittest.main()
