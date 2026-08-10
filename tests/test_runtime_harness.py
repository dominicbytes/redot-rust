import unittest

from tools.run_fixture import evaluate_initialization, evaluate_markers, evaluate_run
from tools.scan_redot_log import scan_text


class LogScannerTests(unittest.TestCase):
    def test_clean_redot_and_smoke_output_has_no_findings(self) -> None:
        text = (
            "Redot Engine LTS v26.2.stable.official.4f5b14aba\n"
            "REDOT_RUST_SMOKE lifecycle=ready\n"
            "REDOT_RUST_RESULT status=PASS\n"
        )
        self.assertEqual(scan_text(text), [])

    def test_errors_warnings_crashes_and_missing_symbols_are_reported(self) -> None:
        text = "\n".join(
            [
                "ERROR: failed to load extension",
                "WARNING: suspicious behavior",
                "Program crashed with signal 11",
                "Cannot get class 'MissingRustClass'",
            ]
        )
        findings = scan_text(text)
        self.assertEqual([item["severity"] for item in findings], ["error", "warning", "crash", "error"])

    def test_explicit_allowlist_suppresses_only_matching_line(self) -> None:
        findings = scan_text(
            "WARNING: allow this\nWARNING: keep this", allowlist=["allow this"]
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("keep this", findings[0]["line"])


class ResultEvaluatorTests(unittest.TestCase):
    def test_required_markers_are_fail_closed(self) -> None:
        markers = ("stage=Scene", "tool=PASS")
        self.assertEqual(
            evaluate_markers("stage=Scene\ntool=PASS", markers, "editor"), []
        )
        self.assertEqual(
            evaluate_markers("stage=Scene", markers, "editor"),
            ["editor marker is missing: tool=PASS"],
        )

    def valid_result(self) -> dict:
        return {
            "status": "PASS",
            "frames": 2,
            "assertions": {"class_visible": True, "method": True, "signal": True},
        }

    def test_complete_result_passes(self) -> None:
        self.assertEqual(evaluate_run(0, False, self.valid_result(), []), [])

    def test_process_failure_timeout_short_run_assertion_and_log_are_all_failures(self) -> None:
        result = self.valid_result()
        result["frames"] = 1
        result["assertions"]["signal"] = False
        findings = [{"severity": "warning", "line": "WARNING: test"}]

        failures = evaluate_run(7, True, result, findings)

        self.assertIn("process exit code 7", failures)
        self.assertIn("wall-clock timeout", failures)
        self.assertIn("fixture ran 1 frame(s); at least 2 are required", failures)
        self.assertIn("assertion failed: signal", failures)
        self.assertIn("log warning: WARNING: test", failures)

    def test_missing_result_fails(self) -> None:
        self.assertEqual(
            evaluate_run(0, False, None, []), ["fixture result JSON is missing"]
        )

    def test_editor_initialization_is_gated_separately(self) -> None:
        self.assertEqual(evaluate_initialization(0, False, []), [])
        failures = evaluate_initialization(
            11, True, [{"severity": "crash", "line": "signal 11"}]
        )
        self.assertEqual(
            failures,
            [
                "editor initialization exit code 11",
                "editor initialization wall-clock timeout",
                "editor initialization log crash: signal 11",
            ],
        )


if __name__ == "__main__":
    unittest.main()
