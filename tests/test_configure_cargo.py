import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.configure_cargo import prepare_environment, remap_environment
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

    def _interface_lock(self, **values: object) -> None:
        lock = json.loads(self.lock_path.read_text())
        lock["files"]["gdextension_interface_json"] = values
        self.lock_path.write_text(json.dumps(lock))

    def test_bundled_lock_rejects_interface_override(self) -> None:
        interface = self.root / "interface.json"
        interface.write_text("{}")
        with self.assertRaisesRegex(IdentityError, "bundled-only"):
            prepare_environment(self.lock_path, self.api_path, interface, {})

    def test_explicit_interface_is_hashed_and_reported(self) -> None:
        interface = self.root / "interface.json"
        interface.write_text("{}")
        digest = hashlib.sha256(interface.read_bytes()).hexdigest().upper()
        self._interface_lock(mode="explicit", sha256=digest)
        env, report = prepare_environment(self.lock_path, self.api_path, interface, {})
        self.assertEqual(env["GDRUST_GODOT_INTERFACE_JSON"], str(interface.resolve()))
        self.assertEqual(report["interface"], {"mode": "explicit", "sha256": digest})
        self.assertEqual(report["companion_sha256"]["gdextension_interface_json"], digest)
        interface.write_text('{"changed": true}')
        with self.assertRaisesRegex(IdentityError, "lock is stale"):
            prepare_environment(self.lock_path, self.api_path, interface, {})

    def test_interface_requires_mode_hash_file_and_valid_json(self) -> None:
        interface = self.root / "interface.json"
        interface.write_text("invalid JSON")
        for fields, pattern in (({"available": True}, "mode"),
                                ({"mode": "explicit"}, "SHA-256"),
                                ({"mode": "explicit", "sha256": "0" * 64}, "lock is stale")):
            with self.subTest(fields=fields):
                self._interface_lock(**fields)
                with self.assertRaisesRegex(IdentityError, pattern):
                    prepare_environment(self.lock_path, self.api_path, interface, {})
        self._interface_lock(mode="explicit", sha256=hashlib.sha256(interface.read_bytes()).hexdigest())
        with self.assertRaisesRegex(IdentityError, "invalid JSON"):
            prepare_environment(self.lock_path, self.api_path, interface, {})
        with self.assertRaisesRegex(IdentityError, "requires --interface-json"):
            prepare_environment(self.lock_path, self.api_path, None, {})

    def test_remaps_all_build_roots_and_preserves_caller_flags(self) -> None:
        roots = {"CARGO_HOME": self.root / "cargo cache", "RUSTUP_HOME": self.root / "rustup home",
                 "CARGO_TARGET_DIR": self.root / "build output"}
        for base, prefix in (({"RUSTFLAGS": "--cfg custom -C opt-level=1"}, ["--cfg", "custom", "-C", "opt-level=1"]),
                             ({"RUSTFLAGS": "ignored", "CARGO_ENCODED_RUSTFLAGS": "--cfg\x1fcustom=\"with spaces\""},
                              ["--cfg", 'custom="with spaces"'])):
            env = remap_environment({**base, **{k: str(v) for k, v in roots.items()},
                                     "CARGO_BUILD_TARGET": "x86_64-pc-windows-msvc"}, self.root)
            flags = env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
            self.assertEqual(flags[:len(prefix)], prefix)
            for root in [self.root, *roots.values()]:
                self.assertTrue(any(flag.startswith(f"--remap-path-prefix={root.resolve()}=") for flag in flags))
            self.assertIn("link-arg=/PDBALTPATH:%_PDB%", flags)
        env = remap_environment({"CARGO_BUILD_TARGET": "x86_64-unknown-linux-gnu"}, self.root)
        self.assertNotIn("PDBALTPATH", env["CARGO_ENCODED_RUSTFLAGS"])
        # A `--target` argument decides over the host platform and the environment.
        for command, expected in (
            (["cargo", "build", "--target", "x86_64-unknown-linux-gnu"], False),
            (["cargo", "build", "--target=aarch64-apple-darwin"], False),
            (["cargo", "build", "--target", "x86_64-pc-windows-msvc"], True),
        ):
            with self.subTest(command=command):
                env = remap_environment({"CARGO_BUILD_TARGET": "x86_64-pc-windows-msvc"}, self.root, command)
                self.assertEqual("PDBALTPATH" in env["CARGO_ENCODED_RUSTFLAGS"], expected)

    def test_example_path_is_resolved_by_real_cargo_from_repository_root(self) -> None:
        cargo = shutil.which("cargo")
        if cargo is None:
            self.skipTest("Cargo is required for the config resolution test")
        repo = self.root / "consumer"
        (repo / ".cargo").mkdir(parents=True)
        (repo / "src").mkdir()
        example = Path(__file__).parents[1] / ".cargo/config.toml.example"
        (repo / ".cargo/config.toml").write_bytes(example.read_bytes())
        (repo / "Cargo.toml").write_text('[package]\nname="config-resolution"\nversion="0.0.0"\nedition="2024"\n')
        (repo / "src/main.rs").write_text('fn main() { println!("{}", env!("GDRUST_GODOT_API_JSON")); }')
        env = dict(os.environ)
        for key in ("GDRUST_GODOT_API_JSON", "CARGO_ENCODED_RUSTFLAGS", "RUSTFLAGS"):
            env.pop(key, None)
        result = subprocess.run([cargo, "+1.94.0", "run", "--offline", "-j", "4", "--quiet",
                                 "--target-dir", str(self.root / "config-target")],
                                cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, "Cargo config resolution failed")
        import tomllib
        value = tomllib.loads(example.read_text())["env"]["GDRUST_GODOT_API_JSON"]["value"]
        lock = json.loads((example.parents[1] / "redot-api.lock.json").read_text())
        locked_value = lock["files"]["extension_api"]["path"]
        self.assertEqual(value, locked_value)
        self.assertEqual(Path(result.stdout.strip()).resolve(), (repo / locked_value).resolve())

    def test_stale_api_hash_stops_before_cargo_environment_is_returned(self) -> None:
        self.api_path.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(IdentityError, "lock is stale"):
            prepare_environment(self.lock_path, self.api_path, None, {})


if __name__ == "__main__":
    unittest.main()
