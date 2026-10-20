import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.redot_api import binding_identity, host_target, load_lock, sha256_file
from tools.run_fixture import EDITOR_MARKERS
from tools.run_installed_fixture import InstalledFixtureError, run_installed_fixture
from tools.export_fixture import RUNTIME_MARKERS
from tests.test_runtime_gates import result_for


class InstalledProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "project.godot").write_text("config_version=5")
        cache = self.project / ".godot"
        cache.mkdir()
        for name in ("extension_list.cfg", "global_script_class_cache.cfg"):
            (cache / name).write_text("fresh cache")
        self.lock = load_lock()
        identity = self.lock["certified_engines"]["redot-26.3"]
        self.target = host_target()
        self.runtime = {"profile": "redot-26.3", "target": self.target, "identity": identity,
                        "binary_sha256": next(iter(identity["assets"][self.target]["binaries"].values())),
                        "version_string": identity["redot"]["version_string"]}
        self.proof = {"status": "PASS", "binding_abi": binding_identity(self.lock),
                      "runtime_engine": self.runtime,
                      "initialization": {"markers": dict.fromkeys(EDITOR_MARKERS, True)},
                      "import_cache_sha256": {name: sha256_file(cache / name)
                          for name in ("extension_list.cfg", "global_script_class_cache.cfg")}}
        self.proof_path = self.root / "import.json"
        self.result = result_for("clean-install")
        self.result["binding_abi"] = binding_identity(self.lock)
        self.result["runtime_engine"] = {section: {part: identity[section]["version_" + part]
                                       for part in ("major", "minor", "patch", "status", "build")}
                                       for section in ("redot", "godot_api")}
        self.result["runtime_engine"]["redot"]["hash"] = identity["redot"]["engine_commit"]

    def run_installed(self, proof, diagnostic=""):
        if proof is not None:
            self.proof_path.write_text(json.dumps(proof))
        def process(command, timeout):
            path = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--redot-rust-result=")))
            path.write_text(json.dumps(self.result))
            return 42, False, "\n".join([*RUNTIME_MARKERS, "REDOT_RUST_CLEAN_RESULT status=PASS", diagnostic]), ""
        with patch("tools.run_installed_fixture.verify_engine", return_value=self.runtime), patch(
            "tools.run_installed_fixture.resolve_dump_editor", return_value=SimpleNamespace(editor=self.root / "engine")
        ), patch("tools.run_installed_fixture._run_process", side_effect=process):
            return run_installed_fixture(self.project, self.target, self.root / "evidence",
                                         redot_bin=self.root / "engine", initialize=False,
                                         engine_profile_name="redot-26.3", import_report=self.proof_path if proof else None)

    def test_only_matching_successful_import_proof_can_be_reused(self):
        self.assertEqual(self.run_installed(self.proof)["status"], "PASS")
        mutations = [lambda p: p.update(status="FAIL"), lambda p: p.update(binding_abi={}),
                     lambda p: p["runtime_engine"].update(profile="redot-26.2"),
                     lambda p: p["initialization"]["markers"].pop(EDITOR_MARKERS[0]),
                     lambda p: p["import_cache_sha256"].update({"extension_list.cfg": "stale"})]
        for mutate in mutations:
            proof = copy.deepcopy(self.proof)
            mutate(proof)
            with self.assertRaises(InstalledFixtureError):
                self.run_installed(proof)
        with self.assertRaises(InstalledFixtureError):
            self.run_installed(None)

    def test_installed_runtime_keeps_all_shutdown_diagnostics_strict(self):
        path = Path(__file__).parents[1] / "compat/redot-26.3-import-allowlist.json"
        diagnostics = "\n".join(json.loads(path.read_text())["diagnostics"])
        result = self.run_installed(self.proof, diagnostics)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["findings"]), 7)
