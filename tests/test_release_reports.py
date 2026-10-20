# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import unittest

from tools.dependency_report import normalize_metadata
from tools.sync_gdext import build_sync_report


class ReleaseReportTests(unittest.TestCase):
    def test_dependency_report_fails_closed_on_missing_license(self) -> None:
        report = normalize_metadata(
            {
                "packages": [{"name": "crate", "version": "1.0.0", "source": None}],
                "workspace_members": [],
            }
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["missing_licenses"], ["crate 1.0.0"])

    def test_noop_sync_reports_no_impact_and_applies_nothing(self) -> None:
        lock = {"gdext": {"revision": "abc"}}
        candidate = {
            "revision": "abc",
            "source_clean": True,
            "api_custom_json_feature": True,
            "standard_header_loader": True,
            "license_file": True,
            "cargo_lock": True,
        }
        report = build_sync_report({"binding_abi": lock}, candidate, [])
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["applied"])
        self.assertEqual(set(report["impact"].values()), {"none"})


if __name__ == "__main__":
    unittest.main()
