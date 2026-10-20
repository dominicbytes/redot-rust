import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.engine_gate import (clear_import_caches, evaluate_fixture_identity,
                               evaluate_import_exception, run_process)
from tools.redot_api import IdentityError, binding_identity, inspect_api, load_lock, validate_lock, verify_engine
from tools.run_fixture import EDITOR_MARKERS, evaluate_run, run_fixture
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
        self.config = config
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

    def test_leak_form_covers_every_official_platform_but_crashes_only_windows(self):
        identity = self.runtime["identity"]
        for target in ("linux-x86_64", "macos-universal"):
            with self.subTest(target=target):
                binaries = identity["assets"][target]["binaries"]
                runtime = {**self.runtime, "target": target, "binary_sha256": next(iter(binaries.values()))}
                result = self.evaluate(runtime=runtime)
                self.assertIsNotNone(result)
                self.assertEqual(result["form"], "shutdown-leaks")
                self.assertIsNone(self.evaluate(self.text + "\nERROR: extra diagnostic", runtime=runtime))
                self.assertIsNone(self.evaluate(self.crash_text(), code=3221225477, runtime=runtime))
                self.assertIsNone(self.evaluate(code=-11, runtime=runtime))

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

    def crash_text(self, *, frames=13, complete=True):
        banner = [
            "=" * 64,
            "CrashHandlerException: Program crashed with signal 11",
            "Engine version: Redot Engine v26.3.stable.official (85e5203945af15755dd2f66cb6fdf7545fd88c0b)",
            "Dumping the backtrace. Please include this when reporting the bug on: https://github.com/Redot-Engine/redot-engine/issues",
        ]
        banner += [f"[{i}] error(-1): no debug info in PE/COFF executable" for i in range(1, frames + 1)]
        if complete:
            banner += ["-- END OF C++ BACKTRACE --", "=" * 64]
        return "\n".join([*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem",
                           "[ DONE ] loading_editor_layout", *banner])

    def test_access_violation_accepts_only_observed_crash_grammar(self):
        for code in (3221225477, -1073741819, 0xC0000005):
            for frames, complete in ((13, True), (3, False), (0, False)):
                with self.subTest(code=code, frames=frames, complete=complete):
                    result = self.evaluate(self.crash_text(frames=frames, complete=complete), code=code)
                    self.assertIsNotNone(result)
                    self.assertEqual(result["form"], "shutdown-abnormal-exit")
                    self.assertEqual(result["returncode"], code)
                    self.assertIn("CrashHandlerException: Program crashed with signal 11", result["diagnostics"])
        self.assertEqual(self.evaluate()["form"], "shutdown-leaks")
        self.assertIsNotNone(self.evaluate(self.crash_text(), code=3221225477,
                                         command=[arg.replace("--editor", "--import") for arg in self.command]))
        self.assertIsNone(self.evaluate(self.crash_text(), code=3221225477,
                                       command=[arg for arg in self.command if arg != "--editor"]))
        # Shutdown leaks may precede the crash, but no other diagnostics may do so.
        self.assertIsNotNone(self.evaluate(self.text + "\n" + self.crash_text(), code=3221225477))

    def test_separator_only_truncated_handler_requires_all_completed_work(self):
        text = "\n".join([*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem",
                           "[ DONE ] loading_editor_layout", "=" * 64])
        result = self.evaluate(text, code=3221225477)
        self.assertIsNotNone(result)
        self.assertEqual(result["form"], "shutdown-abnormal-exit")
        self.assertTrue(result["crash_handler_truncated"])
        for replacement in ("", "=" * 63, "=" * 65, "other output"):
            self.assertIsNone(self.evaluate(text.replace("=" * 64, replacement), code=3221225477))
        for marker in (*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem", "[ DONE ] loading_editor_layout"):
            self.assertIsNone(self.evaluate(text.replace(marker, ""), code=3221225477))
        for extra in ("ERROR: unrelated", "WARNING: unrelated", "FATAL: unrelated"):
            self.assertIsNone(self.evaluate(extra + "\n" + text, code=3221225477))
            self.assertIsNone(self.evaluate(text + "\n" + extra, code=3221225477))
        self.assertIsNone(self.evaluate(text, code=7))
        self.assertIsNone(self.evaluate(text, code=0))
        self.assertIsNone(self.evaluate(text, code=3221225477, context="runtime"))
        self.assertIsNone(self.evaluate(text, code=3221225477,
                                       runtime={**self.runtime, "profile": "redot-26.2"}))
        self.assertIsNone(self.evaluate(text, code=3221225477,
                                       runtime={**self.runtime, "binary_sha256": "0" * 64}))
        clear_import_caches(self.project)
        self.assertIsNone(self.evaluate(text, code=3221225477))

    def test_access_violation_rejects_incomplete_or_foreign_work(self):
        text = self.crash_text()
        for marker in (*EDITOR_MARKERS, "[ DONE ] first_scan_filesystem", "[ DONE ] loading_editor_layout"):
            with self.subTest(marker=marker):
                self.assertIsNone(self.evaluate(text.replace(marker, ""), code=3221225477))
        for code in (0, 1, 7, 42, -11, 139, -1073740791):
            self.assertIsNone(self.evaluate(text, code=code))
        for extra in ("ERROR: unrelated", "WARNING: unrelated", "FATAL: unrelated"):
            self.assertIsNone(self.evaluate(text + "\n" + extra, code=3221225477))
            self.assertIsNone(self.evaluate(extra + "\n" + text, code=3221225477))
        for runtime in ({**self.runtime, "profile": "redot-26.2"},
                        {**self.runtime, "binary_sha256": "0" * 64},
                        {**self.runtime, "version_string": "26.3.stable.custom_build.85e520394"},
                        {**self.runtime, "target": "linux-x86_64"},
                        {**self.runtime, "target": "macos-universal"}):
            self.assertIsNone(self.evaluate(text, code=3221225477, runtime=runtime))
        self.assertIsNone(self.evaluate(text, code=3221225477, timed_out=True))
        for context in ("runtime", "installed", "exported"):
            self.assertIsNone(self.evaluate(text, code=3221225477, context=context))
        for extra in ("--script", "main.gd", "main.tscn", "--export-release", "--"):
            self.assertIsNone(self.evaluate(text, code=3221225477, command=[*self.command, extra]))
        for cache in ("extension_list.cfg", "global_script_class_cache.cfg"):
            path = self.cache / cache
            original = path.read_text()
            for replacement in (None, "invalid", "list=[unexpected]"):
                if replacement is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(replacement)
                self.assertIsNone(self.evaluate(text, code=3221225477))
            path.write_text(original)

    def test_crash_banner_cannot_replace_a_completed_import(self):
        text = self.crash_text()
        header = "CrashHandlerException: Program crashed with signal 11"
        for changed in (text.replace(header, ""),
                        text.replace("signal 11", "signal 6"),
                        text.replace("[2] error", "[9] error"),
                        text.replace("no debug info", "different error"),
                        text.replace("85e5203945af15755dd2f66cb6fdf7545fd88c0b", "wrong"),
                        text + "\n[14] error(-1): no debug info in PE/COFF executable"):
            self.assertIsNone(self.evaluate(changed, code=3221225477))
        self.assertIsNone(self.evaluate(text[text.index(header):] + "\n" + self.text, code=3221225477))
        # Even perfect runtime assertions and this exit must fail all runtime gates.
        for kind in ("smoke", "clean-install", "exported-smoke", "exported-clean-install"):
            self.assertTrue(evaluate_run(3221225477, False, result_for(kind), scan_text(text), fixture_type=kind))

    def test_import_harness_preserves_abnormal_exit_in_visible_pass_report(self):
        def process(command, timeout):
            self.assertFalse((self.cache / "extension_list.cfg").exists())
            self.assertFalse((self.cache / "global_script_class_cache.cfg").exists())
            (self.cache / "extension_list.cfg").write_text("res://smoke.gdextension\n")
            (self.cache / "global_script_class_cache.cfg").write_text("list=[]")
            return 3221225477, False, self.crash_text(), ""
        with patch("tools.run_fixture.verify_engine", return_value=self.runtime), patch(
            "tools.run_fixture.resolve_dump_editor", return_value=SimpleNamespace(editor=self.project / "engine")
        ), patch("tools.run_fixture._run_process", side_effect=process):
            report = run_fixture(self.project, self.project / "engine", self.project / "evidence",
                                 initialize_only=True, engine_profile_name="redot-26.3")
        self.assertEqual(report["status"], "PASS", report["failures"])
        self.assertEqual(report["initialization"]["returncode"], 3221225477)
        self.assertEqual(report["initialization"]["engine_import_exception"]["form"], "shutdown-abnormal-exit")
        self.assertTrue(report["initialization"]["findings"])
        self.assertEqual(len(report["import_cache_sha256"]), 2)

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
