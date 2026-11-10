"""A failed AWACS request may retry only after live absence is proved."""

import json
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_recovery import resolve_uncertain_awacs
from rules.awacs_triggers import next_trigger
from runtime.app.daemon import AutomaticMissionRunner
from runtime.mozi.mission_catalog import read_blue_missions


AWACS = "aaaaaaaa-0000-0000-0000-000000000001"
SIDE = "bbbbbbbb-0000-0000-0000-000000000001"
NAME = "E-2K #1 空中预警"


class AwacsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.situation = {
            "api_base": "http://127.0.0.1:8050/ApiCtrl/api",
            "side": {"guid": SIDE, "name": "蓝方"},
            "scenario": {"start_time": "2026-11-10 08:00:00"},
            "units": {"air": [{"guid": AWACS, "name": "E-2K预警机",
                              "in_facility": True, "airborne": False}]},
            "contacts": {"air": []}, "existing_missions": [],
            "mission_assignments": {},
        }
        self.state = {"uncertain_awacs": True, "uncertain": {},
                      "awacs_opening_attempt": ["old-signature"]}

    def test_legacy_uncertain_record_clears_only_after_absence(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            config = Path(folder) / "task.json"
            config.write_text(json.dumps({"tasks": {"airEarlyWarning": {
                "enabled": True, "unitGuids": [AWACS]}}}), encoding="utf-8")
            self.state["uncertain"][NAME] = {"config": str(config)}
            result = resolve_uncertain_awacs(self.state, self.situation, [])
        self.assertEqual(result, "cleared")
        self.assertFalse(self.state["uncertain_awacs"])
        self.assertNotIn(NAME, self.state["uncertain"])
        self.assertNotIn("awacs_opening_attempt", self.state)
        self.assertEqual(next_trigger(self.situation, self.state)["kind"], "opening")

    def test_matching_live_task_or_busy_aircraft_keeps_lock(self):
        self.state["uncertain"][NAME] = {
            "skill_id": "air.early_warning", "unit_guids": [AWACS]}
        present = [{"guid": "cccccccc-0000-0000-0000-000000000001",
                    "name": NAME, "type": "Support"}]
        self.assertIn("已有同名", resolve_uncertain_awacs(self.state, self.situation, present))
        self.assertTrue(self.state["uncertain_awacs"])
        self.situation["mission_assignments"][AWACS] = present[0]["guid"]
        self.assertIn("已分配", resolve_uncertain_awacs(self.state, self.situation, []))
        self.assertIn(NAME, self.state["uncertain"])

    def test_failed_catalog_read_keeps_lock_and_explains_skip(self):
        self.state["uncertain"][NAME] = {
            "skill_id": "air.early_warning", "unit_guids": [AWACS]}
        runner = object.__new__(AutomaticMissionRunner)
        runner.state = self.state
        lines = []
        runner.output = lines.append
        with patch("runtime.app.daemon.read_blue_missions", side_effect=OSError("offline")):
            runner._reconcile_uncertain_awacs(self.situation)
        self.assertTrue(self.state["uncertain_awacs"])
        self.assertIn("任务列表核对失败", next_trigger(self.situation, self.state)["reason"])
        self.assertEqual(len(lines), 1)

    def test_mission_catalog_requires_successful_complete_response(self):
        present = {"code": 0, "data": [{"id": "cccccccc-0000-0000-0000-000000000001",
                                       "name": NAME, "type": "Support"}]}
        with patch("runtime.mozi.mission_catalog.urlopen",
                   return_value=BytesIO(json.dumps(present).encode("utf-8"))):
            self.assertEqual(read_blue_missions(self.situation["api_base"], SIDE)[0]["name"], NAME)
        with patch("runtime.mozi.mission_catalog.urlopen",
                   return_value=BytesIO(b'{"code":101,"data":null}')):
            with self.assertRaisesRegex(RuntimeError, "未成功返回"):
                read_blue_missions(self.situation["api_base"], SIDE)


if __name__ == "__main__":
    unittest.main()
