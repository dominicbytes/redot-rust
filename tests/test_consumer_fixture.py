# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import tempfile
import unittest
from pathlib import Path

from tools.run_fixture import EXPECTED_ASSERTIONS, RUNTIME_SUCCESS_EXIT
from tools.run_consumer_fixture import (
    ENGINE_MARKER,
    RESULT_MARKER,
    ConsumerFixtureError,
    evaluate_consumer_result,
    materialize_consumer_fixture,
    partition_findings,
)


class ConsumerFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("project.godot", "rapier.gdextension", "main.gd", "main.tscn"):
            (self.source / name).write_text(name, encoding="utf-8")
        self.library = self.root / "consumer.dll"
        self.library.write_bytes(b"consumer")

    def test_materialize_copies_only_selected_library(self) -> None:
        (self.source / ".godot").mkdir()

        result = materialize_consumer_fixture(
            self.source, self.root / "output", self.library
        )

        self.assertEqual(result["library_size"], len(b"consumer"))
        self.assertEqual(
            (self.root / "output" / "bin" / "godot_rapier.dll").read_bytes(),
            b"consumer",
        )
        self.assertFalse((self.root / "output" / ".godot").exists())

    def test_materialize_rejects_existing_output(self) -> None:
        output = self.root / "output"
        output.mkdir()

        with self.assertRaisesRegex(ConsumerFixtureError, "already exists"):
            materialize_consumer_fixture(self.source, output, self.library)

    def test_evaluate_accepts_complete_pass(self) -> None:
        result = {
            "assertion_schema_version": 1,
            "fixture_type": "consumer",
            "dimension": "2d",
            "active_server_class": "RapierPhysicsServer2D",
            "status": "PASS",
            "frames": 90,
            "assertions": dict.fromkeys(EXPECTED_ASSERTIONS["consumer"], True),
        }
        log = f"{ENGINE_MARKER}\n{RESULT_MARKER}\n"

        self.assertEqual(evaluate_consumer_result(RUNTIME_SUCCESS_EXIT, False, result, [], log), [])

    def test_evaluate_rejects_failed_assertion_and_markers(self) -> None:
        result = {
            "status": "FAIL",
            "frames": 1,
            "assertions": {"physics": False},
        }

        failures = evaluate_consumer_result(2, False, result, [], "")

        self.assertTrue(any("process exit code 2" in failure for failure in failures))
        self.assertIn("assertion failed: physics", failures)
        self.assertTrue(
            any("runtime marker is missing" in failure for failure in failures)
        )

    def test_partition_findings_allows_only_exact_typed_signal_notice(self) -> None:
        findings = [
            {
                "line": "WARNING: godot-rust: TypedSignal connections are now auto-disconnected.",
                "line_number": 1,
                "severity": "warning",
            },
            {"line": "ERROR: broken", "line_number": 2, "severity": "error"},
        ]

        blocking, allowed = partition_findings(findings)

        self.assertEqual([finding["line"] for finding in blocking], ["ERROR: broken"])
        self.assertEqual(len(allowed), 1)


if __name__ == "__main__":
    unittest.main()
