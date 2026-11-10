import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_area import _distance_km
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


class AwacsAreaTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.airfield = {
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方起飞机场", "unit_type": "Facility",
            "lat": 20.3, "lon": 130.3}
        self.situation["units"]["army"].append(self.airfield)
        self.call = {
            "call_id": "awacs-near-base", "skill_id": "air.early_warning",
            "mission_name": "机场附近预警", "unit_guids": [
                "aaaaaaaa-0000-0000-0000-000000000004"],
            "area": {"pattern": "racetrack"},
        }

    def area(self):
        return validate_plan({"version": 1, "skill_calls": [self.call]},
                             self.situation, self.registry)[0].area

    def test_unknown_axis_stays_at_airfield_east_west(self):
        self.situation["contacts"]["air"] = []
        area = self.area()
        self.assertEqual(len(area["points"]), 14)
        self.assertIn(self.airfield["guid"], area["remark"])
        self.assertIn("东西向", area["remark"])
        lat_span = max(p["lat"] for p in area["points"]) - min(p["lat"] for p in area["points"])
        lon_span = max(p["lon"] for p in area["points"]) - min(p["lon"] for p in area["points"])
        self.assertGreater(lon_span, lat_span)
        self.assertAlmostEqual(lat_span * 111.2, 30, delta=2)
        self.assertAlmostEqual(lon_span * 111.2 * 0.938, 60, delta=3)
        self.assertTrue(all(_distance_km((20.3, 130.3), (p["lat"], p["lon"])) <= 31
                            for p in area["points"]))

    def test_east_attack_axis_moves_forward_and_long_axis_north_south(self):
        self.call["area"] = {"pattern": "racetrack", "attack_bearing_deg": 90,
                             "forward_km": 200}
        area = self.area()
        lat_span = max(p["lat"] for p in area["points"]) - min(p["lat"] for p in area["points"])
        lon_span = max(p["lon"] for p in area["points"]) - min(p["lon"] for p in area["points"])
        self.assertGreater(lat_span, lon_span)
        self.assertGreater(min(p["lon"] for p in area["points"]), 130.3)
        self.assertTrue(all(_distance_km((20.3, 130.3), (p["lat"], p["lon"])) <= 300
                            for p in area["points"]))

    def test_sole_enemy_early_warning_contact_orients_racetrack(self):
        self.situation["contacts"]["air"] = [{
            "guid": "dddddddd-0000-0000-0000-000000000001",
            "name": "卡-29RLD预警直升机", "unit_type": "Aircraft",
            "lat": 20.3, "lon": 134.5,
        }]
        area = self.area()
        self.assertEqual(area["deployment"]["mode"], "threat")
        self.assertAlmostEqual(area["deployment"]["bearing_deg"], 90, delta=2)
        self.assertEqual(area["deployment"]["forward_km"], 0)
        self.assertIn("疑似进攻轴向", area["remark"])
        lat_span = max(p["lat"] for p in area["points"]) - min(p["lat"] for p in area["points"])
        lon_span = max(p["lon"] for p in area["points"]) - min(p["lon"] for p in area["points"])
        self.assertGreater(lat_span, lon_span)

    def test_max_forward_preserves_300_km_limit(self):
        self.call["area"] = {"pattern": "racetrack", "attack_bearing_deg": 45,
                             "forward_km": 284}
        self.assertLessEqual(max(_distance_km((20.3, 130.3), (p["lat"], p["lon"]))
                                 for p in self.area()["points"]), 300)

    def test_nearest_carrier_is_selected_over_airport(self):
        carrier = {"guid": "bbbbbbbb-0000-0000-0000-000000000003",
                   "name": "蓝方航空母舰", "unit_type": "Ship", "lat": 20.3, "lon": 130.31}
        self.situation["units"]["navy"].append(carrier)
        self.airfield["lat"] = 18.0
        self.assertIn(carrier["guid"], self.area()["remark"])

    def test_missing_platform_and_invalid_deployment_fail(self):
        self.situation["units"]["army"].remove(self.airfield)
        with self.assertRaisesRegex(ValueError, "没有蓝方机场或航母"):
            self.area()
        self.situation["units"]["army"].append(self.airfield)
        for area in ({"pattern": "racetrack", "forward_km": 100},
                     {"pattern": "racetrack", "attack_bearing_deg": 0, "forward_km": 285},
                     {"points": [{"lat": 20, "lon": 130}]}):
            self.call["area"] = area
            with self.assertRaises(ValueError):
                self.area()

    def test_fighter_cannot_be_assigned_to_early_warning(self):
        self.call["unit_guids"] = ["aaaaaaaa-0000-0000-0000-000000000001"]
        with self.assertRaisesRegex(ValueError, "只能分配预警机"):
            self.area()


if __name__ == "__main__":
    unittest.main()
