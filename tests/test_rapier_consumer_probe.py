# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import tempfile
import unittest
from pathlib import Path

from tools.probe_rapier_consumer import GODOT_PACKAGES, inspect_gdext_lock, inspect_runtime, payload_hash
from tests.test_runtime_gates import result_for


class RapierConsumerProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_payload_hash_ignores_cargo_lock(self) -> None:
        left = self.root / "left"
        right = self.root / "right"
        for directory in (left, right):
            (directory / "src").mkdir(parents=True)
            (directory / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
            (directory / "rust-toolchain.toml").write_text("[toolchain]\n", encoding="utf-8")
            (directory / "src" / "lib.rs").write_text("pub fn x() {}\n", encoding="utf-8")
        (left / "Cargo.lock").write_text("left", encoding="utf-8")
        (right / "Cargo.lock").write_text("right", encoding="utf-8")

        self.assertEqual(payload_hash(left), payload_hash(right))

    def test_inspect_gdext_lock_requires_all_packages_at_exact_revision(self) -> None:
        revision = "a" * 40
        lock = self.root / "Cargo.lock"
        package_blocks = []
        for name in (
            "godot",
            "godot-bindings",
            "godot-cell",
            "godot-codegen",
            "godot-core",
            "godot-ffi",
            "godot-macros",
        ):
            package_blocks.append(
                "[[package]]\n"
                f'name = "{name}"\n'
                'version = "0.5.5"\n'
                f'source = "git+https://github.com/godot-rust/gdext?rev={revision}#{revision}"\n'
            )
        lock.write_text("\n".join(package_blocks), encoding="utf-8")

        result = inspect_gdext_lock(lock, revision, "0.5.5")

        self.assertEqual(result["failures"], [])
        self.assertEqual(len(result["packages"]), 7)

    def test_mixed_duplicate_or_spoofed_binding_sources_fail(self):
        revision = "a" * 40
        good_source = f"git+https://github.com/godot-rust/gdext?rev={revision}#{revision}"
        def row(name, source=good_source, version="0.5.5"):
            return f'[[package]]\nname="{name}"\nversion="{version}"\nsource="{source}"\n'
        base = "\n".join(row(name) for name in GODOT_PACKAGES)
        lock = self.root / "Cargo.lock"
        controls = [
            base + row("godot", "registry+https://github.com/rust-lang/crates.io-index", "0.4.5"),
            row("godot", good_source.replace(revision, "b" * 40)) + base,
            base + row("godot"),
            base.replace(revision, "b" * 40),
            base.replace('version="0.5.5"', 'version="0.4.5"'),
            "",
            base + '[[package]]\nname="godot"\nversion="0.5.5"\n',
        ]
        for wrong in (
            good_source.replace("github.com", "github.com.example.org"),
            good_source.replace("/gdext?", "/gdext-fork?"),
            good_source.replace("?rev=", "?branch="),
            good_source.replace("git+https", "https"),
            good_source + "suffix", good_source.replace("#", "&extra=1#"),
        ):
            controls.append(base.replace(good_source, wrong))
        for control in controls:
            with self.subTest(control=control):
                lock.write_text(control, encoding="utf-8")
                report = inspect_gdext_lock(lock, revision, "0.5.5")
                self.assertTrue(report["failures"])
                if control:
                    self.assertGreaterEqual(sum(map(len, report["packages"].values())), 7)

    def test_binding_repository_is_selected_from_the_lock(self):
        revision = "a" * 40
        repository = "https://example.org/fictional/bindings"
        lock = self.root / "Cargo.lock"
        lock.write_text("\n".join(
            f'[[package]]\nname="{name}"\nversion="0.5.5"\n'
            f'source="git+{repository}?rev={revision}#{revision}"\n'
            for name in GODOT_PACKAGES
        ))
        self.assertEqual(inspect_gdext_lock(lock, revision, "0.5.5", repository)["failures"], [])
        self.assertTrue(inspect_gdext_lock(lock, revision, "0.5.5")["failures"])

    def test_consumer_runtime_rejects_empty_and_incomplete_evidence(self):
        path = self.root / "runtime.json"
        valid = {
            "schema_version": 1, "status": "PASS", "selection": {"library_sha256": "CONTROL"},
            "returncode": 42, "timed_out": False, "failures": [], "findings": [],
            "result": result_for("consumer"),
        }
        path.write_text(json.dumps(valid))
        self.assertEqual(inspect_runtime(path, "CONTROL")["failures"], [])
        controls = [{"status": "PASS", "selection": {"library_sha256": "CONTROL"}},
                    {**valid, "result": {}}, {**valid, "returncode": 0}]
        for key in ("assertions", "frames", "dimension", "active_server_class"):
            controls.append({**valid, "result": {**valid["result"], key: None}})
        for control in controls:
            with self.subTest(control=control):
                path.write_text(json.dumps(control))
                self.assertTrue(inspect_runtime(path, "CONTROL")["failures"])
        path.write_text(json.dumps(valid))
        self.assertTrue(inspect_runtime(path, "CONTROL", "3d")["failures"])


if __name__ == "__main__":
    unittest.main()
