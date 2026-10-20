import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.run_fixture import (
    EXPECTED_ASSERTIONS, RUNTIME_SUCCESS_EXIT, RUNTIME_MARKERS,
    evaluate_run, run_fixture,
)
from tools.scan_redot_log import EvidenceSanitizer, scan_evidence, scan_text


def result_for(kind):
    return {
        "assertion_schema_version": 1,
        "fixture_type": kind.removeprefix("exported-"),
        "status": "PASS", "frames": 2,
        "assertions": dict.fromkeys(EXPECTED_ASSERTIONS[kind], True),
        **({"dimension": "2d", "active_server_class": "RapierPhysicsServer2D"}
           if kind == "consumer" else {}),
    }


class RuntimeGateRegressionTests(unittest.TestCase):
    def test_frame_cap_cannot_replace_fixture_quit(self):
        # Negative control: PASS JSON and markers survive, but quit is removed.
        for kind in EXPECTED_ASSERTIONS:
            with self.subTest(kind=kind):
                result = result_for(kind)
                self.assertEqual(evaluate_run(42, False, result, [], fixture_type=kind), [])
                self.assertTrue(evaluate_run(0, False, result, [], fixture_type=kind))
                self.assertTrue(evaluate_run(42, True, result, [], fixture_type=kind))

    def test_removed_tests_empty_missing_unknown_and_non_true_assertions_fail(self):
        for kind in EXPECTED_ASSERTIONS:
            valid = result_for(kind)
            for replacement in (None, {}, {next(iter(valid["assertions"])): True},
                                {**valid["assertions"], "unknown": True},
                                dict.fromkeys(valid["assertions"], 1)):
                with self.subTest(kind=kind, assertions=replacement):
                    result = {**valid, "assertions": replacement}
                    self.assertTrue(evaluate_run(42, False, result, [], fixture_type=kind))
            for key, value in (("assertion_schema_version", None),
                               ("assertion_schema_version", 2), ("frames", None),
                               ("frames", True), ("fixture_type", "unknown")):
                with self.subTest(kind=kind, field=key):
                    self.assertTrue(evaluate_run(42, False, {**valid, key: value}, [], fixture_type=kind))

    def test_consumer_requires_backend_server_and_dimension(self):
        valid = result_for("consumer")
        for key in ("server_class_registered", "factory_class_registered", "active_server_is_rapier"):
            result = copy.deepcopy(valid)
            del result["assertions"][key]
            self.assertTrue(evaluate_run(42, False, result, [], fixture_type="consumer"))
        for key in ("dimension", "active_server_class"):
            for value in (None, "3d", "PhysicsServer2D"):
                self.assertTrue(evaluate_run(42, False, {**valid, key: value}, [], fixture_type="consumer"))

    def test_smoke_harness_rejects_removed_quit_with_pass_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            def capped_process(command, timeout):
                (project / ".results" / "fixture-result.json").write_text(json.dumps(result_for("smoke")))
                return 0, False, "\n".join(RUNTIME_MARKERS), ""
            with patch("tools.run_fixture.resolve_dump_editor", return_value=SimpleNamespace(editor=root / "engine")), patch(
                "tools.run_fixture._run_process", side_effect=capped_process
            ):
                report = run_fixture(project, root / "engine", root / "evidence", initialize=False)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any("completion requires" in failure for failure in report["failures"]))


class EvidencePrivacyTests(unittest.TestCase):
    def test_file_uris_map_known_roots_and_redact_unknown_paths(self):
        sanitizer = EvidenceSanitizer({
            "/fictional/project": "<PROJECT_ROOT>",
            "Q:/fictional/project": "<PROJECT_ROOT>",
        })
        for uri in ("file:///fictional/project/main.gd",
                    "file://localhost/fictional/project/main.gd",
                    "file:///Q:/fictional/project/main.gd",
                    "file:/Q:/fictional/project/main.gd"):
            with self.subTest(uri=uri):
                self.assertEqual(sanitizer.text(uri), "<PROJECT_ROOT>/main.gd")
        for uri in ("file:///home/fictional/private/main.gd",
                    "file:///Users/fictional/private folder/main.gd",
                    "file://localhost/Users/fictional/private%20folder/main.gd",
                    "file:///Q:/fictional/private/main.gd"):
            with self.subTest(uri=uri):
                self.assertEqual(sanitizer.text(uri), "<ABSOLUTE_PATH>")
                findings = scan_text(uri + ": ERROR: failed to load", sanitizer=sanitizer)
                self.assertEqual([item["severity"] for item in findings], ["error"])
                self.assertNotIn("fictional", json.dumps(findings))
        public = "https://example.org/fictional/project/main.gd res://main.gd user://data.json"
        self.assertEqual(sanitizer.text(public), public)
        self.assertEqual(sanitizer.text("file:///fictional/private/main.gd " + public),
                         "<ABSOLUTE_PATH> " + public)

    def test_final_scan_detects_file_uris_and_preserves_public_uris(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "diagnostic.log"
            for uri in ("file:///home/fictional/private/main.gd",
                        "file:///Users/fictional/private folder/main.gd",
                        "file://localhost/fictional/private/main.gd"):
                with self.subTest(uri=uri):
                    path.write_text("ERROR: " + uri, encoding="utf-8")
                    self.assertTrue(scan_evidence(root))
            path.write_text("https://example.org/path res://main.gd user://data.json", encoding="utf-8")
            self.assertEqual(scan_evidence(root), [])

    def test_escaped_json_keys_and_nested_values_are_scanned_and_sanitized(self):
        escaped_path = r"\u0051:\u002ffictional\u002fprivate\u002fmain.gd"
        for payload in ('{"nested": [{"path": "' + escaped_path + '"}]}',
                        '{"' + escaped_path + '": {"safe": true}}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "engine-result.json"
                path.write_text(payload, encoding="utf-8")
                self.assertTrue(scan_evidence(root))
                report = EvidenceSanitizer().finish(root, "report.json", {"status": "PASS"})
                self.assertEqual(report["status"], "PASS")
                sanitized = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("<ABSOLUTE_PATH>", json.dumps(sanitized))
                self.assertNotIn("fictional", json.dumps(sanitized))
                self.assertEqual(scan_evidence(root), [])

    def test_json_placeholder_backslash_suffix_remains_valid_and_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.json"
            sanitizer = EvidenceSanitizer({"Q:/fictional/project": "<PROJECT_ROOT>"})
            sanitizer.write_json(path, {"path": "Q:\\fictional\\project\\main.gd"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"path": "<PROJECT_ROOT>\\main.gd"})
            self.assertEqual(scan_evidence(root), [])

    def test_failure_evidence_is_safe_for_always_upload(self):
        uri = "file:///Users/fictional/private folder/main.gd"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unhandled.log").write_text("ERROR: " + uri, encoding="utf-8")
            (root / "engine-result.json").write_text(
                r'{"path": "\u0051:\u002ffictional\u002fprivate\u002fmain.gd"}',
                encoding="utf-8",
            )
            self.assertTrue(scan_evidence(root))
            report = EvidenceSanitizer().finish(root, "report.json", {
                "status": "FAIL", "failures": ["log error: " + uri],
            })
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(scan_evidence(root), [])
            for path in root.iterdir():
                self.assertNotIn("fictional", path.read_text(encoding="utf-8"))

    def test_runtime_file_uri_failure_sanitizes_engine_json_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            def process(command, timeout):
                result = result_for("smoke")
                result["detail"] = "file:///Users/fictional/private folder/main.gd"
                encoded = json.dumps(result).replace("file:", r"\u0066ile:")
                (project / ".results" / "fixture-result.json").write_text(encoded)
                return 42, False, "\n".join(RUNTIME_MARKERS) + "\nERROR: " + result["detail"], ""
            with patch("tools.run_fixture.resolve_dump_editor", return_value=SimpleNamespace(editor=root / "engine")), patch(
                "tools.run_fixture._run_process", side_effect=process
            ):
                report = run_fixture(project, root / "engine", root / "evidence", initialize=False)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual([item["severity"] for item in report["findings"]], ["error"])
            self.assertEqual(scan_evidence(root / "evidence"), [])
            for path in (root / "evidence").iterdir():
                self.assertNotIn("fictional", path.read_text(encoding="utf-8"))

    def test_known_roots_and_unknown_absolute_paths_are_sanitized(self):
        sanitizer = EvidenceSanitizer({
            "/fictional/project": "<PROJECT_ROOT>", "/fictional/engine": "<ENGINE_ROOT>",
            "/fictional/build": "<BUILD_ROOT>", "/fictional/cache": "<CACHE_ROOT>",
            "/fictional/temp": "<TEMP_ROOT>", "/fictional/person": "<HOME>",
        })
        for root, placeholder in sanitizer.roots:
            if root.startswith("/fictional/"):
                self.assertEqual(sanitizer.text(root + "/file.gd"), placeholder + "/file.gd")
        for value in ("Q:/fictional/private/main.gd", "Q:\\fictional\\private\\main.gd",
                      "\\\\fictional-host\\share\\main.gd", "/unmapped/private/main.gd",
                      "/fictional/private folder/main.gd"):
            self.assertEqual(sanitizer.text(value), "<ABSOLUTE_PATH>")
        self.assertEqual(sanitizer.text("res://main.gd https://example.org/path"),
                         "res://main.gd https://example.org/path")

    def test_scanner_never_copies_resolved_diagnostic_paths(self):
        findings = scan_text("ERROR: /fictional/private/main.gd")
        self.assertEqual(len(findings), 1)
        self.assertNotIn("/fictional", json.dumps(findings))

    def test_redaction_cannot_hide_a_diagnostic_after_an_absolute_filename(self):
        findings = scan_text("Q:/fictional/private/main.gd: ERROR: failed to load")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "error")

    def test_final_scan_covers_unknown_suffixes_and_utf16(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unknown.data").write_bytes("Q:/fictional/private/main.gd".encode("utf-16-le"))
            self.assertTrue(scan_evidence(root))
            report = EvidenceSanitizer().finish(root, "report.json", {"status": "PASS", "failures": []})
            self.assertEqual(report["status"], "FAIL")

    def test_runtime_logs_findings_failures_and_result_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            def process(command, timeout):
                result = result_for("smoke")
                result["detail"] = str(project / "main.gd")
                (project / ".results" / "fixture-result.json").write_text(json.dumps(result))
                return 42, False, "\n".join(RUNTIME_MARKERS) + "\nERROR: " + str(project / "main.gd"), ""
            with patch("tools.run_fixture.resolve_dump_editor", return_value=SimpleNamespace(editor=root / "engine")), patch(
                "tools.run_fixture._run_process", side_effect=process
            ):
                report = run_fixture(project, root / "engine", root / "evidence", initialize=False)
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("<PROJECT_ROOT>", json.dumps(report))
            self.assertNotIn(str(root), json.dumps(report))
            self.assertEqual(scan_evidence(root / "evidence"), [])

    def test_final_evidence_scan_fails_and_cleans_an_unhandled_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "unhandled.log").write_text("ERROR: /fictional/private/main.gd")
            self.assertTrue(scan_evidence(root))
            report = EvidenceSanitizer().finish(root, "report.json", {"status": "PASS", "failures": []})
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(scan_evidence(root), [])
