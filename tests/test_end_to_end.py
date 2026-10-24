from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliEndToEndTests(unittest.TestCase):
    def test_minimal_workspace_produces_expected_audit_structure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="traceveil-e2e-") as temporary:
            temp = Path(temporary)
            workspace = temp / "workspace"
            shutil.copytree(ROOT / "examples" / "minimal", workspace)
            report_path = temp / "report.json"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + environment.get("PYTHONPATH", "")

            result = subprocess.run(
                [sys.executable, "-m", "traceveil", "audit", "--workspace", str(workspace), "--output", str(report_path)],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"], {
                "requests": 2,
                "clear": 0,
                "action_required": 1,
                "not_applicable": 1,
                "indeterminate": 0,
            })
            self.assertEqual([item["request_id"] for item in report["results"]], ["req-1001", "req-1002"])

            authorized, non_deletion = report["results"]
            self.assertEqual(authorized["decision"], "ACTION_REQUIRED")
            self.assertEqual(
                {finding["kind"] for finding in authorized["findings"]},
                {"DATA_REMAINS", "IDENTIFIERS_REMAIN", "ACTIVE_RECREATION_PATH"},
            )
            self.assertIn("DELETE_RECORD", {step["action"] for step in authorized["remediation"]})
            self.assertIn("DISABLE_FLOW", {step["action"] for step in authorized["remediation"]})
            self.assertIn("REQUIRED_RECORD_PRESERVED", {step["check"] for step in authorized["verification"]})
            self.assertEqual(non_deletion["decision"], "NOT_APPLICABLE")
            self.assertEqual(non_deletion["findings"], [])


if __name__ == "__main__":
    unittest.main()
