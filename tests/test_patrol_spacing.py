"""Patrol-area separation at the final multi-Agent validation boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.patrol_spacing import (MIN_CAP_CAP_GAP_KM,
                                  MIN_CAP_AWACS_GAP_KM,
                                  patrol_spacing_advisory,
                                  validate_patrol_spacing)
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


CAP_ONE = "aaaaaaaa-0000-0000-0000-000000000001"
CAP_TWO = "aaaaaaaa-0000-0000-0000-000000000002"
STRIKER = "aaaaaaaa-0000-0000-0000-000000000003"
AWACS = "aaaaaaaa-0000-0000-0000-000000000004"
MISSION_ONE = "99999999-0000-0000-0000-000000000001"
AREA_ONE = "88888888-0000-0000-0000-000000000001"


def rectangle(south, west, north, east):
    return [{"lat": south, "lon": west}, {"lat": south, "lon": east},
            {"lat": north, "lon": east}, {"lat": north, "lon": west}]


def existing(name, type_id, points, mission, area_guid=AREA_ONE):
    return {"guid": area_guid, "name": name, "type_id": type_id,
            "points": points, "mission_guids": [mission]}


def call(name, skill_id, points, mission_guid=""):
    return SimpleNamespace(skill_id=skill_id, mission_name=name,
                           existing_mission_guid=mission_guid,
                           area={"points": points})


class PatrolSpacingTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.west = rectangle(20.0, 130.0, 20.2, 130.2)

    @staticmethod
    def cap(label, unit, points, existing_mission_guid=""):
        return {"call_id": label, "skill_id": "air.combat_patrol",
                "mission_name": label, "unit_guids": [unit],
                "existing_mission_guid": existing_mission_guid,
                "area": {"points": points}, "reason": "覆盖指定空域"}

    def validate(self, calls):
        return validate_plan({"version": 1, "skill_calls": calls},
                             self.situation, self.registry)

    def test_same_batch_caps_cannot_overlap_or_leave_too_small_gap(self):
        second = self.cap("cap-east", CAP_TWO,
                          rectangle(20.0, 130.1, 20.2, 130.3))
        with self.assertRaisesRegex(ValueError, "巡逻区间距不足.*重叠"):
            self.validate([self.cap("cap-west", CAP_ONE, self.west), second])
        second["area"]["points"] = rectangle(20.0, 130.4, 20.2, 130.6)
        with self.assertRaisesRegex(ValueError, "至少需要 160 km"):
            self.validate([self.cap("cap-west", CAP_ONE, self.west), second])
        second["area"]["points"] = rectangle(20.0, 132.0, 20.2, 132.2)
        self.assertEqual(len(self.validate([self.cap("cap-west", CAP_ONE, self.west),
                                            second])), 2)

    def test_same_batch_cap_cannot_contain_awacs_racetrack(self):
        self.situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方机场", "unit_type": "Facility", "lat": 20.3, "lon": 130.3})
        cap = self.cap("cap-local", CAP_ONE,
                       rectangle(19.8, 129.8, 20.8, 130.8))
        awacs = {"call_id": "awacs-local", "skill_id": "air.early_warning",
                 "mission_name": "awacs-local", "unit_guids": [AWACS],
                 "area": {"pattern": "racetrack"}, "reason": "建立附近预警"}
        with self.assertRaisesRegex(ValueError, "巡逻区间距不足.*至少需要 50 km"):
            self.validate([cap, awacs])

    def test_existing_cap_conflicts_with_new_cap_but_own_update_excludes_old_area(self):
        self.situation["existing_areas"] = [
            existing("old-cap", "405", self.west, MISSION_ONE)]
        with self.assertRaisesRegex(ValueError, "巡逻区间距不足"):
            self.validate([self.cap("new-cap", CAP_TWO,
                                    rectangle(20.0, 130.3, 20.2, 130.5))])
        moved = self.cap("old-cap", CAP_ONE,
                         rectangle(20.0, 131.0, 20.2, 131.2), MISSION_ONE)
        self.assertEqual(len(self.validate([moved])), 1)

    def test_existing_awacs_conflicts_with_cap_and_missing_geometry_fails_closed(self):
        self.situation["existing_areas"] = [existing(
            "active-awacs", "407", rectangle(20.0, 130.0, 20.2, 130.2),
            MISSION_ONE)]
        with self.assertRaisesRegex(ValueError, "至少需要 50 km"):
            self.validate([self.cap("new-cap", CAP_ONE,
                                    rectangle(20.0, 130.4, 20.2, 130.6))])
        self.situation["existing_areas"][0]["points"] = []
        with self.assertRaisesRegex(ValueError, "缺少几何"):
            self.validate([self.cap("new-cap", CAP_ONE,
                                    rectangle(20.0, 131.0, 20.2, 131.2))])

    def test_preexisting_overlap_does_not_block_pure_strike(self):
        self.situation["existing_areas"] = [
            existing("old-cap", "405", self.west, MISSION_ONE),
            existing("other-cap", "405", rectangle(20.0, 130.1, 20.2, 130.3),
                     "99999999-0000-0000-0000-000000000002",
                     "88888888-0000-0000-0000-000000000002")]
        strike = {"call_id": "strike-sea", "skill_id": "air.sea_strike",
                  "mission_name": "strike-sea", "unit_guids": [STRIKER, "aaaaaaaa-0000-0000-0000-000000000090"],
                  "target_contact_guids": ["eeeeeeee-0000-0000-0000-000000000001"],
                  "reason": "攻击当前接触"}
        self.assertEqual(len(self.validate([strike])), 1)
        advice = patrol_spacing_advisory(self.situation["existing_areas"])
        self.assertEqual(advice["minimum_gaps_km"]["405-405"], MIN_CAP_CAP_GAP_KM)
        self.assertEqual(advice["minimum_gaps_km"]["405-407"], MIN_CAP_AWACS_GAP_KM)
        self.assertEqual(len(advice["conflicts"]), 1)
        self.assertTrue(advice["conflicts"][0]["overlap"])

    def test_existing_area_reuse_resolves_geometry_and_shared_old_area_remains(self):
        original = existing("shared", "405", self.west, MISSION_ONE)
        original["mission_guids"].append("99999999-0000-0000-0000-000000000002")
        reused = call("new-cap", "air.combat_patrol", [])
        reused.area = {"existing_area_guid": AREA_ONE}
        with self.assertRaisesRegex(ValueError, "巡逻区间距不足"):
            validate_patrol_spacing([reused], [original])
        moved = call("updated-cap", "air.combat_patrol",
                     rectangle(20.0, 130.1, 20.2, 130.3), MISSION_ONE)
        with self.assertRaisesRegex(ValueError, "巡逻区间距不足"):
            validate_patrol_spacing([moved], [original])

    def test_update_requires_readback_of_original_area(self):
        moved = call("updated-cap", "air.combat_patrol",
                     rectangle(20.0, 131.0, 20.2, 131.2), MISSION_ONE)
        with self.assertRaisesRegex(ValueError, "原任务的当前巡逻区域未回读到"):
            validate_patrol_spacing([moved], [])

    def test_simultaneous_updates_replace_both_old_regions(self):
        other_mission = "99999999-0000-0000-0000-000000000002"
        old = [existing("old-west", "405", self.west, MISSION_ONE),
               existing("old-east", "405",
                        rectangle(20.0, 130.1, 20.2, 130.3), other_mission,
                        "88888888-0000-0000-0000-000000000002")]
        moved = [call("new-west", "air.combat_patrol",
                      rectangle(20.0, 131.0, 20.2, 131.2), MISSION_ONE),
                 call("new-east", "air.combat_patrol",
                      rectangle(20.0, 133.0, 20.2, 133.2), other_mission)]
        validate_patrol_spacing(moved, old)

    def test_gap_override_is_configurable(self):
        west = call("west", "air.combat_patrol", self.west)
        east = call("east", "air.combat_patrol",
                    rectangle(20.0, 130.4, 20.2, 130.6))
        with self.assertRaisesRegex(ValueError, "至少需要 160 km"):
            validate_patrol_spacing([west, east], [])
        validate_patrol_spacing([west, east], [], minimums={"405-405": 20})

    def _eastward_situation(self, *, contacts=2):
        self.situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方机场", "unit_type": "Facility", "lat": 20.0, "lon": 130.0})
        self.situation["contacts"] = {"air": [
            {"guid": f"dddddddd-0000-0000-0000-{index:012d}",
             "lat": 20.0 + index * 0.02, "lon": 134.0 + index * 0.02}
            for index in range(1, contacts + 1)],
            "sea": [], "land": [], "submarine": []}
        return self.situation

    def test_awacs_must_be_behind_cap_on_consistent_enemy_axis(self):
        situation = self._eastward_situation()
        cap = call("cap-east", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        awacs_front = call("awacs-front", "air.early_warning",
                           rectangle(20.0, 132.0, 20.1, 132.1))
        with self.assertRaisesRegex(ValueError, "预警区位置过前.*一致敌情方位"):
            validate_patrol_spacing([cap, awacs_front], [], situation=situation)
        awacs_rear = call("awacs-rear", "air.early_warning",
                          rectangle(20.0, 130.0, 20.1, 130.1))
        validate_patrol_spacing([cap, awacs_rear], [], situation=situation)

    def test_single_enemy_aew_is_not_confirmed_axis_uses_platform_fallback(self):
        situation = self._eastward_situation(contacts=1)
        situation["contacts"]["air"][0]["name"] = "敌方预警机"
        cap = call("cap-east", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        awacs = call("awacs-front", "air.early_warning",
                     rectangle(20.0, 132.0, 20.1, 132.1))
        with self.assertRaisesRegex(ValueError, "预警区位置过前.*起飞平台至战斗巡逻区"):
            validate_patrol_spacing([cap, awacs], [], situation=situation)

    def test_rear_offset_and_two_thirds_vertex_boundary(self):
        situation = self._eastward_situation()
        cap = call("cap-east", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        # The 1 degree north offset satisfies the 50 km edge gap so this test
        # isolates the rear-position geometry rather than the gap check.
        awacs = call("awacs", "air.early_warning",
                     rectangle(21.0, 130.7, 21.1, 130.9))
        with self.assertRaisesRegex(ValueError, "预警区位置过前.*至少需 30 km"):
            validate_patrol_spacing([cap, awacs], [], situation=situation)
        awacs.area["points"] = rectangle(21.0, 130.6, 21.1, 130.8)
        validate_patrol_spacing([cap, awacs], [], situation=situation)
        awacs.area["points"] = rectangle(21.0, 130.0, 21.1, 131.3)
        with self.assertRaisesRegex(ValueError, "后方顶点 2/4"):
            validate_patrol_spacing([cap, awacs], [], situation=situation)

    def test_different_attack_sector_cap_does_not_use_east_axis(self):
        situation = self._eastward_situation()
        cap_north = call("cap-north", "air.combat_patrol",
                         rectangle(22.0, 130.0, 22.1, 130.1))
        awacs_east = call("awacs-east", "air.early_warning",
                          rectangle(20.0, 132.0, 20.1, 132.1))
        validate_patrol_spacing([cap_north, awacs_east], [], situation=situation)

    def test_missing_platform_is_advisory_not_hard_rejection(self):
        cap = call("cap-east", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        awacs = call("awacs-front", "air.early_warning",
                     rectangle(20.0, 132.0, 20.1, 132.1))
        validate_patrol_spacing([cap, awacs], [], situation=self.situation)
        rows = [existing("cap-east", "405", cap.area["points"], MISSION_ONE),
                existing("awacs-front", "407", awacs.area["points"],
                         "99999999-0000-0000-0000-000000000002",
                         "88888888-0000-0000-0000-000000000002")]
        self.assertTrue(patrol_spacing_advisory(rows, situation=self.situation)["warnings"])

    def test_validate_plan_integrates_rear_rule_for_generated_awacs_area(self):
        self._eastward_situation()
        cap = self.cap("cap-east", CAP_ONE,
                       rectangle(20.0, 131.0, 20.1, 131.1))
        awacs = {"call_id": "awacs-forward", "skill_id": "air.early_warning",
                 "mission_name": "awacs-forward", "unit_guids": [AWACS],
                 "area": {"pattern": "racetrack", "attack_bearing_deg": 90,
                          "forward_km": 180},
                 "reason": "区域侦察"}
        with self.assertRaisesRegex(ValueError, "预警区位置过前"):
            self.validate([cap, awacs])

    def test_awacs_bound_launch_platform_takes_priority_over_nearest_platform(self):
        situation = self._eastward_situation(contacts=0)
        launch = situation["units"]["army"][-1]["guid"]
        situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000003",
            "name": "另一机场", "unit_type": "Facility", "lat": 20.0, "lon": 132.0})
        cap = call("cap-mid", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        awacs = call("awacs-front", "air.early_warning",
                     rectangle(20.0, 131.9, 20.1, 132.0))
        # With no binding, the AWACS's nearest platform is the eastern airport
        # and it is behind the CAP measured from that airport.
        validate_patrol_spacing([cap, awacs], [], situation=situation)
        awacs.area["deployment"] = {"platform_guid": launch}
        with self.assertRaisesRegex(ValueError, "预警区位置过前"):
            validate_patrol_spacing([cap, awacs], [], situation=situation)

    def test_existing_awacs_uses_saved_platform_and_lost_binding_warns(self):
        situation = self._eastward_situation(contacts=0)
        launch = situation["units"]["army"][-1]["guid"]
        situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000003",
            "name": "另一机场", "unit_type": "Facility", "lat": 20.0, "lon": 132.0})
        awacs_mission = "99999999-0000-0000-0000-000000000002"
        area = existing("awacs-front", "407",
                        rectangle(20.0, 131.9, 20.1, 132.0),
                        awacs_mission,
                        "88888888-0000-0000-0000-000000000002")
        situation["existing_missions"] = [{"guid": awacs_mission,
                                           "skill_id": "air.early_warning",
                                           "deployment": {"platform_guid": launch}}]
        cap = call("cap-mid", "air.combat_patrol",
                   rectangle(20.0, 131.0, 20.1, 131.1))
        with self.assertRaisesRegex(ValueError, "预警区位置过前"):
            validate_patrol_spacing([cap], [area], situation=situation)
        situation["existing_missions"][0]["deployment"]["platform_guid"] = (
            "cccccccc-0000-0000-0000-000000000099")
        validate_patrol_spacing([cap], [area], situation=situation)
        warning = patrol_spacing_advisory([area, existing(
            "cap-mid", "405", cap.area["points"], MISSION_ONE)],
            situation=situation)["warnings"]
        self.assertTrue(any("绑定的起飞平台" in item for item in warning))


if __name__ == "__main__":
    unittest.main()
