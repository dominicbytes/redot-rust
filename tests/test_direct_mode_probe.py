import unittest

from tools.probe_direct_mode import evaluate_direct_probe


class DirectModeProbeTests(unittest.TestCase):
    def test_expected_redot_major_rejection_is_a_pass(self) -> None:
        output = (
            "Only Godot major version 4 is supported; found version "
            "26.2.stable.official.4f5b14aba."
        )
        self.assertEqual(evaluate_direct_probe(101, output, "26.2.stable.official.4f5b14aba"), ("PASS", []))

    def test_selected_lock_drives_expected_product_version(self) -> None:
        for version in ("26.2.stable.official.4f5b14aba", "26.3.stable.official.85e520394"):
            output = "Only Godot major version 4 is supported: " + version
            self.assertEqual(evaluate_direct_probe(101, output, version), ("PASS", []))
        status, failures = evaluate_direct_probe(101, output, "26.2.stable.official.4f5b14aba")
        self.assertEqual(status, "FAIL")
        self.assertIn("locked Redot product version was not present in the rejection", failures)

    def test_success_or_unrelated_failure_is_unresolved(self) -> None:
        status, failures = evaluate_direct_probe(0, "compiled", "26.2.stable.official.4f5b14aba")
        self.assertEqual(status, "FAIL")
        self.assertIn("direct mode unexpectedly succeeded", failures)
        self.assertIn("expected Godot-major rejection was not observed", failures)


if __name__ == "__main__":
    unittest.main()
