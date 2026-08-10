import unittest

from tools.probe_direct_mode import evaluate_direct_probe


class DirectModeProbeTests(unittest.TestCase):
    def test_expected_redot_major_rejection_is_a_pass(self) -> None:
        output = (
            "Only Godot major version 4 is supported; found version "
            "26.2.stable.official.4f5b14aba."
        )
        self.assertEqual(evaluate_direct_probe(101, output), ("PASS", []))

    def test_success_or_unrelated_failure_is_unresolved(self) -> None:
        status, failures = evaluate_direct_probe(0, "compiled")
        self.assertEqual(status, "FAIL")
        self.assertIn("direct mode unexpectedly succeeded", failures)
        self.assertIn("expected Godot-major rejection was not observed", failures)


if __name__ == "__main__":
    unittest.main()
