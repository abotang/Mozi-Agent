import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_triggers import next_trigger
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


OLD = "aaaaaaaa-0000-0000-0000-000000000004"
SPARE = "aaaaaaaa-0000-0000-0000-000000000005"
MISSION = "dddddddd-0000-0000-0000-000000000001"
AREA = "eeeeeeee-0000-0000-0000-000000000001"
ENEMY = "ffffffff-0000-0000-0000-000000000001"


class AwacsTriggerTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.s = {
            "side": {"guid": "bbbbbbbb-0000-0000-0000-000000000001", "name": "蓝方"},
            "scenario": {"initial_time": "2026-11-06 08:00:00", "start_time": "2026-11-06 08:00:00",
                         "end_time": "2026-11-06 16:00:00"},
            "units": {"air": [
                {"guid": OLD, "name": "E-2K预警机", "unit_type": "Aircraft", "lat": 20, "lon": 130,
                 "airborne": False, "in_facility": True},
                {"guid": SPARE, "name": "E-2K预警机-2", "unit_type": "Aircraft", "lat": 20, "lon": 130,
                 "airborne": False, "in_facility": True}],
                "army": [{"guid": "cccccccc-0000-0000-0000-000000000001", "name": "蓝方机场",
                          "unit_type": "Facility", "lat": 20, "lon": 130}], "navy": []},
            "contacts": {"air": [], "sea": [], "land": [], "submarine": []},
            "existing_missions": [],
        }
        self.state = {}

    def mission(self, mode="local", forward=0):
        record = {"guid": MISSION, "name": "机场预警", "skill_id": "air.early_warning",
                "unit_guids": [OLD], "area_guid": AREA,
                "area_points": [{"lat": 20, "lon": 129.8}, {"lat": 20.1, "lon": 130},
                                {"lat": 20, "lon": 130.2}, {"lat": 19.9, "lon": 130}],
                "deployment": {"mode": mode, "forward_km": forward,
                               "platform_guid": "cccccccc-0000-0000-0000-000000000001"},
                "created_at_sim": "2026-11-06 08:00:00"}
        self.s["existing_areas"] = [{
            "guid": AREA, "name": "机场预警区", "type_id": "407",
            "points": copy.deepcopy(record["area_points"]),
            "mission_guids": [MISSION],
        }]
        return record

    def enemy(self, lon=131):
        self.s["contacts"]["air"] = [{"guid": ENEMY, "lat": 20, "lon": lon,
                                      "name": "敌机", "unit_type": "Aircraft"}]

    def test_opening_and_local_twenty_minute_transition(self):
        self.assertEqual(next_trigger(self.s, self.state)["mode"], "local")
        self.s["existing_missions"] = [self.mission()]
        self.s["units"]["air"][0].update(airborne=True, in_facility=False)
        self.s["scenario"]["start_time"] = "2026-11-06 08:19:59"
        self.enemy(lon=140)
        self.assertEqual(next_trigger(self.s, self.state)["kind"], "hold")
        self.s["scenario"]["start_time"] = "2026-11-06 08:20:00"
        self.assertEqual(next_trigger(self.s, self.state)["kind"], "local_to_threat")
        self.s["contacts"]["air"] = []
        self.assertEqual(next_trigger(self.s, self.state)["kind"], "hold")

    def test_retreat_then_relief(self):
        self.s["existing_missions"] = [self.mission("threat", 100)]
        self.s["units"]["air"][0].update(airborne=True, in_facility=False)
        self.enemy()
        event = next_trigger(self.s, self.state)
        self.assertEqual(event["kind"], "threat_retreat")
        self.assertEqual(event["enemy_guid"], ENEMY)
        self.state["awacs_flights"][MISSION]["retreat_pending"] = True
        self.assertEqual(next_trigger(self.s, self.state)["kind"], "hold")
        self.s["units"]["air"][0].update(airborne=False, in_facility=True)
        event = next_trigger(self.s, self.state)
        self.assertEqual(event["kind"], "relief")
        self.assertEqual(event["replacement_guid"], SPARE)

    def test_relief_reuses_area_and_rejects_wrong_aircraft(self):
        self.s["existing_missions"] = [self.mission("threat", 100)]
        self.state["awacs_flights"] = {MISSION: {"airborne_seen": True}}
        event = next_trigger(self.s, self.state)
        self.s["operational_rules"] = {"awacs_trigger": event}
        call = {"call_id": "relief", "skill_id": "air.early_warning",
                "mission_name": "机场预警", "existing_mission_guid": MISSION,
                "unit_guids": [SPARE], "area": {"pattern": "racetrack"},
                "parameters": {"relieved_unit_guid": OLD}}
        result = validate_plan({"version": 1, "skill_calls": [call]}, self.s, self.registry)
        self.assertEqual(result[0].area["existing_area_guid"], AREA)
        bad = copy.deepcopy(call)
        bad["unit_guids"] = [OLD]
        with self.assertRaisesRegex(ValueError, "换班"):
            validate_plan({"version": 1, "skill_calls": [bad]}, self.s, self.registry)

    def test_retreat_can_pass_behind_launch_platform(self):
        self.s["existing_missions"] = [self.mission("local", 0)]
        self.s["units"]["air"][0].update(airborne=True, in_facility=False)
        self.enemy(lon=131)
        event = next_trigger(self.s, self.state)
        self.s["operational_rules"] = {"awacs_trigger": event}
        call = {"call_id": "back", "skill_id": "air.early_warning",
                "mission_name": "机场预警", "existing_mission_guid": MISSION,
                "unit_guids": [OLD], "area": {"pattern": "racetrack",
                                            "attack_bearing_deg": 90, "forward_km": -40}}
        result = validate_plan({"version": 1, "skill_calls": [call]}, self.s, self.registry)
        self.assertEqual(result[0].area["deployment"]["forward_km"], -40)
        bad = copy.deepcopy(call)
        bad["area"]["forward_km"] = 10
        with self.assertRaisesRegex(ValueError, "回撤"):
            validate_plan({"version": 1, "skill_calls": [bad]}, self.s, self.registry)


if __name__ == "__main__":
    unittest.main()
