"""Opening blue ROE runs once per scenario and requires readback confirmation."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.side_doctrine import opening_engagement_needed
from runtime.execution.side_doctrine import set_opening_engagement


class SideDoctrineTests(unittest.TestCase):
    def test_startup_and_rewind_trigger(self):
        state = {"scenario": "blue|opening", "opportunity_targets_scenario": "blue|opening"}
        self.assertFalse(opening_engagement_needed(state))
        self.assertTrue(opening_engagement_needed(state, rewound=True))
        state["scenario"] = "blue|new-opening"
        self.assertTrue(opening_engagement_needed(state))

    def test_script_result_must_confirm_value_one(self):
        situation = {"api_base": "http://127.0.0.1:8050/ApiCtrl/api",
                     "side": {"guid": "11111111-1111-1111-1111-111111111111"}}
        with patch("runtime.execution.side_doctrine.find_powershell", return_value="pwsh"), patch(
                "runtime.execution.side_doctrine.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({
                "success": True, "value": "1", "changed": True}), stderr="")
            result = set_opening_engagement(situation, ROOT / "scripts/powershell/10-开局蓝方接战规则.ps1")
            self.assertTrue(result["changed"])
            self.assertEqual(run.call_args.args[0][-4:], [
                "-ApiBase", situation["api_base"], "-SideGuid", situation["side"]["guid"]])
            run.return_value.stdout = json.dumps({"success": True, "value": "0"})
            with self.assertRaisesRegex(RuntimeError, "未确认"):
                set_opening_engagement(situation, "script.ps1")


if __name__ == "__main__":
    unittest.main()
