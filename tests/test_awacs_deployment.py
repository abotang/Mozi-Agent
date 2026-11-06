import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_deployment import enrich_existing_mission, minimum_separation_km
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


class AwacsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.situation["units"]["air"].append({
            "guid": "aaaaaaaa-0000-0000-0000-000000000005",
            "name": "预警机-2", "unit_type": "Aircraft", "lat": 20.31, "lon": 130.31})
        self.situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方机场", "unit_type": "Facility", "lat": 20.3, "lon": 130.3})
        self.first = self.call("a", "aaaaaaaa-0000-0000-0000-000000000004", 90, 150)
        self.second = self.call("b", "aaaaaaaa-0000-0000-0000-000000000005", 270, 150)

    @staticmethod
    def call(label, guid, bearing, forward):
        return {"call_id": f"awacs-{label}", "skill_id": "air.early_warning",
                "mission_name": f"预警-{label}", "unit_guids": [guid],
                "area": {"pattern": "racetrack", "attack_bearing_deg": bearing,
                         "forward_km": forward},
                "reason": "当前战场两个独立方向需要分别维持空中预警覆盖"}

    def validate(self, calls):
        return validate_plan({"version": 1, "skill_calls": calls},
                             self.situation, self.registry)

    def test_one_awacs_needs_no_second_aircraft_justification(self):
        self.first["reason"] = ""
        self.assertEqual(len(self.validate([self.first])), 1)

    def test_two_awacs_must_have_reason_and_separate_stations(self):
        self.second["reason"] = ""
        with self.assertRaisesRegex(ValueError, "第二架预警机"):
            self.validate([self.first, self.second])
        self.second["reason"] = "当前战场两个独立方向需要分别维持空中预警覆盖"
        self.assertEqual(len(self.validate([self.first, self.second])), 2)
        self.second["area"]["attack_bearing_deg"] = 90
        with self.assertRaisesRegex(ValueError, "预警机间距不足"):
            self.validate([self.first, self.second])

    def test_existing_station_blocks_duplicate_and_third_aircraft(self):
        previous = self.validate([self.first])[0]
        center = {"lat": sum(p["lat"] for p in previous.area["points"]) / 14,
                  "lon": sum(p["lon"] for p in previous.area["points"]) / 14}
        self.situation["existing_missions"] = [{
            "name": "已有预警", "guid": "dddddddd-0000-0000-0000-000000000001",
            "skill_id": "air.early_warning", "area_center": center,
            "unit_guids": self.first["unit_guids"]}]
        self.assertEqual(len(self.validate([self.second])), 1)
        self.second["area"]["attack_bearing_deg"] = 90
        with self.assertRaisesRegex(ValueError, "预警机间距不足"):
            self.validate([self.second])
        self.second["area"]["attack_bearing_deg"] = 270
        self.second["unit_guids"] = self.first["unit_guids"]
        with self.assertRaisesRegex(ValueError, "重复出动"):
            self.validate([self.second])
        self.second["unit_guids"] = ["aaaaaaaa-0000-0000-0000-000000000005"]
        self.situation["existing_missions"].append({
            "name": "已有预警2", "skill_id": "air.early_warning",
            "area_center": {"lat": 19, "lon": 129}})
        with self.assertRaisesRegex(ValueError, "最多同时安排两架"):
            self.validate([self.second])

    def test_separation_grows_with_observed_battlefield(self):
        near = minimum_separation_km(self.situation)
        self.situation["contacts"]["air"].append({
            "guid": "dddddddd-0000-0000-0000-000000000002",
            "name": "远端空中接触", "unit_type": "Aircraft", "lat": 20.3, "lon": 140.3})
        self.assertGreater(minimum_separation_km(self.situation), near)
        self.assertLessEqual(minimum_separation_km(self.situation), 250)

    def test_recover_existing_station_from_saved_config(self):
        area = self.validate([self.first])[0].area
        with TemporaryDirectory(dir=ROOT) as folder:
            run = Path(folder)
            (run / "reports").mkdir()
            (run / "configs").mkdir()
            report = run / "reports" / "001.json"
            (run / "configs" / "001.json").write_text(json.dumps({
                "tasks": {"airEarlyWarning": {"area": area,
                                                "unitGuids": self.first["unit_guids"]}}
            }), encoding="utf-8")
            existing = enrich_existing_mission("已有预警", {
                "guid": "dddddddd-0000-0000-0000-000000000001",
                "skill_id": "air.early_warning", "report": str(report)})
        self.assertEqual(existing["unit_guids"], self.first["unit_guids"])
        self.assertIsNotNone(existing["area_center"])


if __name__ == "__main__":
    unittest.main()
