# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.dependency_report import (
    DependencyReportError, cached_license_files, linked_packages, third_party_bundle,
)


class DependencyBundleTests(unittest.TestCase):
    @staticmethod
    def metadata(root: Path) -> dict:
        packages = []
        for name, kind, source in (("smoke", "cdylib", None), ("linked", "lib", "registry+https://example.invalid/index"),
                                   ("build-helper", "lib", "registry+https://example.invalid/index"),
                                   ("macros", "proc-macro", "registry+https://example.invalid/index"),
                                   ("macro-helper", "lib", "registry+https://example.invalid/index")):
            packages.append({"id": name, "name": name, "version": "1.0.0", "source": source,
                             "license": "MIT", "manifest_path": str(root / name / "Cargo.toml"),
                             "targets": [{"kind": [kind]}]})
        def dep(name: str, kind: str | None = None) -> dict:
            return {"pkg": name, "dep_kinds": [{"kind": kind}]}
        return {"packages": packages, "workspace_members": ["smoke"], "resolve": {"nodes": [
            {"id": "smoke", "deps": [dep("linked"), dep("build-helper", "build"), dep("macros")]},
            {"id": "macros", "deps": [dep("macro-helper")]},
            *[{"id": name, "deps": []} for name in ("linked", "build-helper", "macro-helper")],
        ]}}

    def test_only_normal_target_dependencies_receive_notices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self.metadata(root)
            self.assertEqual([row["name"] for row in linked_packages(metadata)], ["linked", "smoke"])
            cached = root / "linked"
            cached.mkdir()
            (cached / "LICENSE-MIT").write_bytes(b"Exact locked permission and copyright text\n")
            (cached / "NOTICE").write_bytes(b"Exact locked notice\n")
            with patch("tools.dependency_report.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata))) as run:
                one = third_party_bundle(root, ("x86_64-pc-windows-msvc",))
                two = third_party_bundle(root, ("x86_64-pc-windows-msvc",))
            self.assertEqual(one, two)
            command = run.call_args.args[0]
            self.assertIn("--offline", command)
            self.assertIn("--locked", command)
            self.assertIn("x86_64-pc-windows-msvc", command)
            self.assertEqual(one["third-party/linked-1.0.0/LICENSE-MIT"], (cached / "LICENSE-MIT").read_bytes())
            self.assertEqual(one["third-party/linked-1.0.0/NOTICE"], (cached / "NOTICE").read_bytes())
            inventory = json.loads(one["dependency-inventory.json"])
            self.assertEqual([row["name"] for row in inventory["linked_packages"]], ["linked"])
            self.assertEqual(len(inventory["locked_packages"]), 5)
            self.assertNotIn(str(root), one["dependency-inventory.json"].decode())
            (cached / "LICENSE-MIT").unlink()
            with self.assertRaisesRegex(DependencyReportError, "license material missing"):
                cached_license_files(metadata["packages"][1])

    def test_inventory_includes_packages_from_other_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            filtered = self.metadata(root)
            full = self.metadata(root)
            full["packages"].append({"id": "other-platform", "name": "other-platform", "version": "1.0.0",
                                     "source": "registry+https://example.invalid/index", "license": "MIT"})
            (root / "linked").mkdir()
            (root / "linked/LICENSE").write_bytes(b"Locked license")
            responses = [subprocess.CompletedProcess([], 0, json.dumps(value)) for value in (full, filtered)]
            with patch("tools.dependency_report.subprocess.run", side_effect=responses) as run:
                bundle = third_party_bundle(root, ("x86_64-pc-windows-msvc",))
            self.assertNotIn("--filter-platform", run.call_args_list[0].args[0])
            self.assertIn("--filter-platform", run.call_args_list[1].args[0])
            inventory = json.loads(bundle["dependency-inventory.json"])
            self.assertIn("other-platform", [row["name"] for row in inventory["locked_packages"]])
            self.assertEqual([row["name"] for row in inventory["linked_packages"]], ["linked"])

    def test_git_subcrate_inherits_exact_checkout_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "crate").mkdir()
            (root / "LICENSE").write_bytes(b"Locked workspace license")
            package = {"name": "crate", "source": "git+https://example.invalid/project#1234",
                       "manifest_path": str(root / "crate/Cargo.toml"), "license": "MPL-2.0"}
            self.assertEqual(cached_license_files(package), {"LICENSE": b"Locked workspace license"})

    def test_missing_offline_metadata_fails_closed(self) -> None:
        with patch("tools.dependency_report.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "")):
            with self.assertRaisesRegex(DependencyReportError, "offline Cargo metadata failed"):
                third_party_bundle(Path("."), ("x86_64-pc-windows-msvc",))


if __name__ == "__main__":
    unittest.main()
