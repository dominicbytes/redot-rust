import re
import shutil
import subprocess
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
NATIVE_PREFERENCE = "$PSNativeCommandUseErrorActionPreference = $true"


class CiWorkflowTests(unittest.TestCase):
    def test_every_pwsh_step_stops_on_native_failure(self) -> None:
        steps = re.split(r"(?m)^      - ", WORKFLOW.read_text(encoding="utf-8"))[1:]
        pwsh_steps = [step for step in steps if re.search(r"(?m)^        shell: pwsh$", step)]
        self.assertTrue(pwsh_steps)
        for step in pwsh_steps:
            with self.subTest(step=step.splitlines()[0]):
                body = step.split("        run: |\n", 1)[1]
                lines = [line.strip() for line in body.splitlines() if line.strip()]
                self.assertEqual(lines[0], NATIVE_PREFERENCE)
                self.assertEqual(body.count("$PSNativeCommandUseErrorActionPreference"), 1)
                self.assertNotIn("$ErrorActionPreference", body)

    def test_all_platforms_run_unit_format_lint_and_rust_tests(self) -> None:
        jobs = re.split(r"(?m)^  [a-z][a-z0-9-]*:\n", WORKFLOW.read_text(encoding="utf-8").split("jobs:\n")[1])[1:]
        self.assertTrue(jobs)
        jobs = [job for job in jobs if "needs: engine-profiles" in job]
        self.assertEqual(len(jobs), 3)
        for job in jobs:
            for gate in (
                "-m unittest discover -s tests",
                "cargo fmt --all -- --check",
                "cargo clippy --workspace --all-targets --all-features -- -D warnings",
                "cargo test --workspace --all-features",
            ):
                with self.subTest(job=job.splitlines()[0], gate=gate):
                    self.assertIn(gate, job)

    def test_checkout_and_upload_actions_have_versioned_commit_pins(self) -> None:
        actions = re.findall(r"(?m)^      - uses: (actions/(?:checkout|upload-artifact)@.*)$", WORKFLOW.read_text(encoding="utf-8"))
        self.assertTrue(actions)
        for action in actions:
            version = re.search(r"@[0-9a-f]{40} # v([0-9]+)\.[0-9]+\.[0-9]+$", action)
            self.assertIsNotNone(version)
            self.assertGreaterEqual(int(version.group(1)), 7)

    def test_engine_matrix_and_downloads_are_lock_driven(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('json.dumps(list(lock["certified_engines"]))', text)
        self.assertEqual(text.count("engine_profile: ${{ fromJSON("), 3)
        self.assertEqual(text.count("tools/fetch_engine.py --binding"), 3)
        self.assertEqual(text.count("tools/fetch_engine.py --engine-profile"), 3)
        self.assertEqual(text.count("--binding --output-dir build/api"), 3)
        self.assertEqual(text.count("--output-dir build/engine-api"), 3)
        self.assertEqual(text.count("--skip-initialize --import-report"), 6)
        self.assertEqual(text.count("${{ matrix.engine_profile }}-evidence"), 3)
        for version in ("26.2", "26.3"):
            self.assertNotIn(version, text)
        self.assertNotIn("releases/download/", text)

    def test_every_binary_audit_enforces_its_lane_target(self) -> None:
        jobs = re.split(r"(?m)^  [a-z][a-z0-9-]*:\n", WORKFLOW.read_text(encoding="utf-8").split("jobs:\n")[1])[1:]
        for job, target in zip(jobs[1:], ("windows-x86_64", "linux-x86_64", "macos-universal"), strict=True):
            commands = [line for line in job.splitlines() if "tools/verify_binary_deps.py " in line]
            self.assertEqual(len(commands), 2)
            for command in commands:
                self.assertIn("--target " + target + " ", command)
                self.assertIn("${{ matrix.engine_profile }}", command)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_native_failure_cannot_be_replaced_by_later_success(self) -> None:
        # Match GitHub's built-in pwsh wrapper around the workflow's preference.
        prefix = "$ErrorActionPreference = 'stop'\n"
        suffix = "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }"
        commands = 'pwsh -NoProfile -Command "exit 7"\npwsh -NoProfile -Command "exit 0"'
        for preference, should_fail in [("", False), (NATIVE_PREFERENCE + "\n", True)]:
            with self.subTest(native_preference=bool(preference)):
                result = subprocess.run(
                    ["pwsh", "-NoProfile", "-NonInteractive", "-Command", prefix + preference + commands + suffix],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode != 0, should_fail)


if __name__ == "__main__":
    unittest.main()
