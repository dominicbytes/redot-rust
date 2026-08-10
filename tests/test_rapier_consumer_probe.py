# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tools.probe_rapier_consumer import inspect_gdext_lock, payload_hash


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


if __name__ == "__main__":
    unittest.main()
