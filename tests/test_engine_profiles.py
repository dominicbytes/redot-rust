import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.engine_gate import (clear_import_caches, evaluate_fixture_identity,
                               evaluate_import_exception, run_process)
from tools.redot_api import IdentityError, binding_identity, inspect_api, load_lock, validate_lock, verify_engine
from tools.run_fixture import EDITOR_MARKERS, evaluate_run
from tests.test_runtime_gates import result_for
from tools.scan_redot_log import scan_text
from tests.test_api_identity import FIXTURE, make_lock, sha256


class EngineProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = load_lock()

    def test_legacy_mixed_duplicate_and_incomplete_locks_fail(self):
        for value in ({"schema_version": 1}, {**self.lock, "files": {}},
                      {**self.lock, "binding_abi": {}},
                      {**self.lock, "certified_engines": {}},
                      {**self.lock, "schema_version": True}):
            with self.subTest(value=value.get("schema_version")), self.assertRaises(IdentityError):
                validate_lock(value)
        path = self.root / "duplicate.json"
        path.write_text('{"schema_version": 2, "schema_version": 1}')
        with self.assertRaisesRegex(IdentityError, "duplicate"):
            load_lock(path)
        for field in ("assets", "redot", "godot_api", "files", "binding_abi"):
            value = copy.deepcopy(self.lock)
            del value["certified_engines"]["redot-26.3"][field]
            with self.subTest(field=field), self.assertRaises(IdentityError):
                validate_lock(value)

    def test_binding_and_engine_raw_dumps_are_independent(self):
        api = self.root / "extension_api.json"
        api.write_bytes(FIXTURE.read_bytes())
        lock = make_lock(api)
        binding_hash = lock["binding_abi"]["files"]["extension_api"]["sha256"]
        document = json.loads(api.read_text())
        document["redot_header"]["version_minor"] = 3
        api.write_text(json.dumps(document))
        lock["certified_engines"]["redot-26.3"]["files"]["extension_api"]["sha256"] = sha256(api)
        self.assertEqual(inspect_api(api, lock, engine_profile_name="redot-26.3")["purpose"], "engine-audit")
        with self.assertRaisesRegex(IdentityError, "lock is stale"):
            inspect_api(api, lock)
        with self.assertRaises(IdentityError):
            inspect_api(api, lock, engine_profile_name="redot-26.2")
        self.assertEqual(lock["binding_abi"]["files"]["extension_api"]["sha256"], binding_hash)

    def test_binary_hash_and_actual_version_both_gate_profile(self):
        binary = self.root / "engine.exe"
        binary.write_bytes(b"official control")
        profile = self.lock["certified_engines"]["redot-26.3"]
        profile["assets"]["windows-x86_64"]["binaries"] = {"engine.exe": sha256(binary)}
        profile["assets"]["windows-x86_64"]["executable"] = "engine.exe"
        version = profile["redot"]["version_string"]
        with patch("tools.redot_api.subprocess.run", return_value=subprocess.CompletedProcess([], 0, version, "")):
            self.assertEqual(verify_engine(binary, self.lock, "redot-26.3", target="windows-x86_64")["profile"], "redot-26.3")
            with self.assertRaises(IdentityError):
                verify_engine(binary, self.lock, "redot-26.2", target="windows-x86_64")
        for code, output in ((0, "26.3.stable.custom_build.85e520394"), (1, version)):
            with patch("tools.redot_api.subprocess.run", return_value=subprocess.CompletedProcess([], code, output, "")), self.assertRaises(IdentityError):
                verify_engine(binary, self.lock, "redot-26.3", target="windows-x86_64")
        binary.write_bytes(b"patched control")
        with patch("tools.redot_api.subprocess.run") as run, self.assertRaises(IdentityError):
            verify_engine(binary, self.lock, "redot-26.3", target="windows-x86_64")
        run.assert_not_called()

    def test_forged_fixture_runtime_identity_fails(self):
        identity = self.lock["certified_engines"]["redot-26.3"]
        runtime = {"identity": identity}
        actual = {section: {part: identity[section]["version_" + part]
                           for part in ("major", "minor", "patch", "status", "build")}
                  for section in ("redot", "godot_api")}
        actual["redot"]["hash"] = identity["redot"]["engine_commit"]
        result = {"binding_abi": binding_identity(self.lock), "runtime_engine": actual}
        self.assertEqual(evaluate_fixture_identity(result, self.lock, runtime), [])
        for mutate in (lambda r: r.pop("binding_abi"),
                       lambda r: r["runtime_engine"]["redot"].update(minor=2),
                       lambda r: r["runtime_engine"]["redot"].update(hash="forged")):
            changed = copy.deepcopy(result)
            mutate(changed)
            self.assertTrue(evaluate_fixture_identity(changed, self.lock, runtime))

    def test_malformed_fixture_identity_fails_cleanly(self):
        identity = self.lock["certified_engines"]["redot-26.3"]
        for actual in (None, [], {"redot": []}, {"redot": {}, "godot_api": []}):
            self.assertTrue(evaluate_fixture_identity({"runtime_engine": actual}, self.lock, {"identity": identity}))


class ImportExceptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.lock = load_lock()
        identity = self.lock["certified_engines"]["redot-26.3"]
        asset = identity["assets"]["windows-x86_64"]
        self.runtime = {"profile": "redot-26.3", "identity": identity, "target": "windows-x86_64",
                        "version_string": identity["redot"]["version_string"],
                        "binary_sha256": asset["binaries"]["redot.windows.editor.x86_64.llvm.exe"]}
        config = json.loads((Path(__file__).parents[1] / identity["import_exception"]).read_text())
        self.text = "\n".join([*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem",
                                "[ DONE ] loading_editor_layout", *config["diagnostics"]])
        self.command = ["engine", "--headless", "--editor", "--path", str(self.project), "--quit-after", "120"]
        self.cache = self.project / ".godot"
        self.cache.mkdir()
        (self.project / "smoke.gdextension").write_text("descriptor")
        (self.cache / "extension_list.cfg").write_text("res://smoke.gdextension\n")
        (self.cache / "global_script_class_cache.cfg").write_text("list=[]")

    def evaluate(self, text=None, **overrides):
        arguments = {"lock": self.lock, "runtime": self.runtime, "project": self.project,
                     "markers": EDITOR_MARKERS, "context": "editor-import", "command": self.command}
        code = overrides.pop("code", 0)
        timed_out = overrides.pop("timed_out", False)
        arguments.update(overrides)
        return evaluate_import_exception(self.text if text is None else text, code, timed_out, **arguments)

    def test_exact_completed_import_is_visible(self):
        result = self.evaluate()
        self.assertIsNotNone(result)
        self.assertTrue(result["fresh_caches_verified"])
        self.assertEqual(len(result["diagnostics"]), 7)

    def test_extra_diagnostic_missing_marker_wrong_profile_and_runtime_fail(self):
        self.assertIsNone(self.evaluate(self.text + "\nERROR: extra diagnostic"))
        for marker in (*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem", "[ DONE ] loading_editor_layout"):
            self.assertIsNone(self.evaluate(self.text.replace(marker, "")))
        self.assertIsNone(self.evaluate(runtime={**self.runtime, "profile": "redot-26.2"}))
        for context in ("runtime", "installed", "exported"):
            self.assertIsNone(self.evaluate(context=context))
        for extra in ("--script", "scene.tscn", "--export-release", "--"):
            self.assertIsNone(self.evaluate(command=[*self.command, extra]))
        for override in ({"timed_out": True}, {"code": 3221225477}, {"code": 42},
                         {"runtime": {**self.runtime, "binary_sha256": "0" * 64}},
                         {"runtime": {**self.runtime, "target": "linux-x86_64"}}):
            self.assertIsNone(self.evaluate(**override))

    def test_same_shutdown_lines_fail_runtime_installed_and_exported_gates(self):
        findings = scan_text(self.text)
        self.assertEqual(len(findings), 7)
        for kind in ("smoke", "clean-install", "exported-smoke", "exported-clean-install"):
            self.assertTrue(evaluate_run(42, False, result_for(kind), findings, fixture_type=kind))

    def test_missing_cache_or_extension_fails_and_caches_are_freshened(self):
        (self.cache / "extension_list.cfg").write_text("res://wrong.gdextension")
        self.assertIsNone(self.evaluate())
        clear_import_caches(self.project)
        self.assertFalse((self.cache / "extension_list.cfg").exists())
        self.assertIsNone(self.evaluate())


class CaptureTests(unittest.TestCase):
    def test_logger_only_diagnostic_cannot_escape_and_log_path_is_absolute(self):
        def process(command, **kwargs):
            log = Path(command[command.index("--log-file") + 1])
            self.assertTrue(log.is_absolute())
            self.assertLess(command.index("--log-file"), command.index("--"))
            log.write_text("WARNING: logger-only diagnostic")
            return subprocess.CompletedProcess(command, 42, "console marker", "")
        with patch("tools.engine_gate.subprocess.run", side_effect=process):
            code, timeout, stdout, stderr = run_process(["engine", "--headless", "--quit-after", "120", "--", "user-argument"], 30)
        self.assertEqual(code, 42)
        self.assertFalse(timeout)
        self.assertIn("logger-only diagnostic", stdout)
