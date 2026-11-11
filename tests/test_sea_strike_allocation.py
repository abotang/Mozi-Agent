"""Sea strike distribution, aircraft constraints, and planner integration."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.model_client import AgentConfig
from rules.sea_strike_allocation import allocation, apply_allocation
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


def unit_id(index):
    return f"aaaaaaaa-0000-0000-0000-{index:012d}"


def target_id(index):
    return f"eeeeeeee-0000-0000-0000-{index:012d}"


def situation(aircraft=8, targets=3):
    return {
        "side": {"name": "蓝方", "guid": "11111111-1111-1111-1111-111111111111"},
        "scenario": {"start_time": "2026-11-06 10:00:00", "end_time": "2026-11-06 22:00:00"},
        "units": {"air": [
            {"guid": unit_id(i), "name": f"F-16A #{i}", "unit_type": "Aircraft",
             "lat": 0., "lon": 0., "strike_capabilities": ["sea", "air"],
             "weapon_info_status": "ok"} for i in range(1, aircraft + 1)],
            "navy": [], "army": [], "submarine": []},
        "contacts": {"sea": [
            {"guid": target_id(i), "name": f"敌舰-{i}", "unit_type": "Ship",
             "lat": 0., "lon": 1.} for i in range(1, targets + 1)],
            "air": [], "land": [], "submarine": []},
        "mission_assignments": {}, "existing_missions": [], "existing_areas": [],
        "operational_rules": {"awacs_trigger": {"kind": "hold"}},
    }


def sea_call(target=1, units=(1, 2)):
    return {"call_id": f"sea-{target}", "skill_id": "air.sea_strike",
            "mission_name": f"对海-{target}", "unit_guids": [unit_id(i) for i in units],
            "target_contact_guids": [target_id(target)]}


class SeaStrikeAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SkillRegistry(ROOT / "skills").load()

    def assert_unique_distribution(self, report, expected_counts):
        groups = report["groups"]
        self.assertEqual([len(g["unit_guids"]) for g in groups], expected_counts)
        allocated = [u for g in groups for u in g["unit_guids"]]
        self.assertEqual(len(allocated), len(set(allocated)))

    def test_eight_aircraft_three_targets_integer_balance(self):
        result = allocation(situation(8, 3))
        self.assert_unique_distribution(result, [3, 3, 2])
        self.assertEqual(result["waiting_unit_guids"], [])

    def test_five_aircraft_three_targets_prioritize_two_groups(self):
        result = allocation(situation(5, 3), priority=[target_id(3), target_id(2)])
        self.assert_unique_distribution(result, [3, 2])
        self.assertEqual([g["target_contact_guid"] for g in result["groups"]],
                         [target_id(3), target_id(2)])
        self.assertEqual(result["deferred_target_guids"], [target_id(1)])

    def test_single_aircraft_or_no_contacts_cannot_create_group(self):
        self.assertEqual(allocation(situation(1, 3))["groups"], [])
        result = allocation(situation(4, 0))
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["waiting_unit_guids"], [unit_id(i) for i in range(1, 5)])

    def test_only_free_known_anti_ship_fighters_enter_pool(self):
        data = situation(8, 1)
        data["mission_assignments"] = {"{" + unit_id(1).upper() + "}": "some-task"}
        data["units"]["air"][1]["weapon_info_status"] = "unknown"
        data["units"]["air"][2]["strike_capabilities"] = ["air"]
        data["units"]["air"][3]["name"] = "E-2D预警机"
        data["units"]["air"][4]["name"] = "EC-130H电子干扰机"
        data["units"]["air"][5].pop("strike_capabilities")
        result = allocation(data)
        self.assertEqual(result["groups"][0]["unit_guids"], [unit_id(7), unit_id(8)])

    def test_geographic_matching_preserves_aircraft_with_only_one_target(self):
        data = situation(4, 2)
        # Flexible aircraft reach both targets, constrained aircraft reach only A.
        data["contacts"]["sea"][0]["lon"] = 0.
        data["contacts"]["sea"][1]["lon"] = 16.
        for row in data["units"]["air"][:2]:
            row["lon"] = 8.
        for row in data["units"]["air"][2:]:
            row["lon"] = -2.
        result = allocation(data, priority=[target_id(1), target_id(2)])
        self.assert_unique_distribution(result, [2, 2])
        by_target = {g["target_contact_guid"]: g["unit_guids"] for g in result["groups"]}
        self.assertEqual(by_target[target_id(1)], [unit_id(3), unit_id(4)])
        self.assertEqual(by_target[target_id(2)], [unit_id(1), unit_id(2)])

    def test_already_covered_target_is_not_recreated(self):
        data = situation(4, 2)
        data["active_air_sea_target_guids"] = [target_id(1)]
        calls, report = apply_allocation([sea_call()], data)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["target_contact_guids"], [target_id(2)])
        self.assertEqual(len(calls[0]["unit_guids"]), 4)

    def test_other_calls_and_escorts_reserve_their_aircraft(self):
        other = {"call_id": "keep", "skill_id": "air.air_strike", "mission_name": "keep",
                 "unit_guids": [unit_id(1)], "escort_unit_guids": [unit_id(2)]}
        proposed = [other, sea_call(units=(3,))]
        before = deepcopy(proposed)
        calls, report = apply_allocation(proposed, situation(6, 2))
        self.assertEqual(calls[0], other)
        self.assert_unique_distribution(report, [2, 2])
        self.assertEqual(set(u for c in calls[1:] for u in c["unit_guids"]),
                         {unit_id(i) for i in range(3, 7)})
        self.assertEqual(proposed, before)

    def test_no_sea_intent_creates_nothing_and_duplicate_intents_merge(self):
        calls, report = apply_allocation([], situation())
        self.assertEqual(calls, [])
        self.assertIsNone(report)
        proposal = sea_call(units=(1,))
        duplicate = {**proposal, "call_id": "duplicate", "mission_name": "duplicate"}
        calls, report = apply_allocation([proposal, duplicate], situation(4, 1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["unit_guids"]), 4)
        self.assertEqual(len(report["original_calls"]), 2)

    def test_existing_mission_update_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_allocation([{**sea_call(), "existing_mission_guid": unit_id(99)}], situation())

    def test_invalid_sea_target_preserves_other_tasks_without_retargeting(self):
        other = {"skill_id": "air.combat_patrol", "call_id": "keep", "unit_guids": [unit_id(4)]}
        for bad in (sea_call(target=99), {**sea_call(), "target_contact_guids": []}):
            calls, report = apply_allocation([other, bad], situation(4, 2))
            self.assertEqual(calls, [other])
            self.assertEqual(report["groups"], [])
            self.assertEqual(len(report["rejected_calls"]), 1)
            self.assertEqual(len(report["waiting_unit_guids"]), 3)

    def test_bad_sea_intent_does_not_discard_valid_sea_intent(self):
        calls, report = apply_allocation([sea_call(target=99), sea_call()], situation(4, 2))
        self.assertEqual(len(report["rejected_calls"]), 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual({c["target_contact_guids"][0] for c in calls}, {target_id(1), target_id(2)})
        self.assertTrue(all(len(c["unit_guids"]) == 2 for c in calls))

    def test_generated_name_does_not_reuse_any_existing_mission(self):
        data = situation(2, 1)
        data["existing_missions"] = [
            {"guid": unit_id(90 + i), "name": name, "skill_id": "air.sea_strike"}
            for i, name in enumerate(("对海-1", "对海-1-2", "对海-1-3"))]
        calls, report = apply_allocation([sea_call()], data)
        self.assertEqual(calls[0]["mission_name"], "对海-1-4")
        self.assertFalse(calls[0].get("existing_mission_guid"))
        self.assertNotIn(calls[0]["mission_name"], {m["name"] for m in data["existing_missions"]})
        self.assertEqual(report["original_calls"][0]["mission_name"], "对海-1")

    def test_collision_suffix_keeps_call_id_within_100_characters(self):
        data = situation(4, 2)
        proposed = [{**sea_call(target=target), "call_id": "x" * 100} for target in (1, 2)]
        calls, report = apply_allocation(proposed, data)
        self.assertEqual([call["call_id"] for call in calls], ["x" * 100, "x" * 98 + "-2"])
        self.assertEqual(len(validate_plan({"version": 1, "skill_calls": calls}, data,
                                          self.registry)), 2)

    def test_validation_rejects_singleton_multi_target_and_any_out_of_range_plane(self):
        data = situation(2, 2)
        cases = [(sea_call(units=(1,)), "至少分配2架"),
                 ({**sea_call(), "target_contact_guids": [target_id(1), target_id(2)]}, "一个目标")]
        for call, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_plan({"version": 1, "skill_calls": [call]}, data, self.registry)
        data["units"]["air"][1]["lon"] = 20.
        with self.assertRaisesRegex(ValueError, "每架对海飞机.*1000公里"):
            validate_plan({"version": 1, "skill_calls": [sea_call()]}, data, self.registry)

    def test_case_variants_of_one_guid_do_not_satisfy_two_aircraft_minimum(self):
        call = sea_call()
        call["unit_guids"] = [unit_id(1), unit_id(1).upper()]
        with self.assertRaisesRegex(ValueError, "至少分配2架"):
            validate_plan({"version": 1, "skill_calls": [call]}, situation(2, 1), self.registry)

    def test_validation_rejects_occupied_and_already_covered_target(self):
        data = situation(2, 1)
        data["mission_assignments"] = {unit_id(1): unit_id(99)}
        with self.assertRaisesRegex(ValueError, "飞机已被任务占用"):
            validate_plan({"version": 1, "skill_calls": [sea_call()]}, data, self.registry)
        data["mission_assignments"] = {}
        data["active_air_sea_target_guids"] = [target_id(1)]
        with self.assertRaisesRegex(ValueError, "已有打击任务"):
            validate_plan({"version": 1, "skill_calls": [sea_call()]}, data, self.registry)

    def test_planner_applies_rule_once_preserves_patrol_and_records_trace(self):
        data = situation(5, 2)
        patrol = {"call_id": "patrol", "skill_id": "air.combat_patrol", "mission_name": "巡逻",
                  "unit_guids": [unit_id(1)], "area": {"name": "巡逻区", "points": [
                      {"lat": 0., "lon": 0.}, {"lat": 0., "lon": .2},
                      {"lat": .2, "lon": .2}, {"lat": .2, "lon": 0.}]}}

        class Client:
            def __init__(self):
                self.roles, self.events = [], []

            def plan(self, role, system, context):
                self.roles.append(role)
                if role == "joint":
                    return {"version": 1, "mission_intents": [], "main_effort": "test"}
                if role == "component_air":
                    return {"version": 1, "service": "air", "summary": "巡逻和对海打击",
                            "skill_calls": [deepcopy(patrol), sea_call(units=(2,))]}
                raise AssertionError(f"unexpected model call {role}")

            def trace_event(self, role, event, **values):
                self.events.append((role, event, values))

        client = Client()
        planner = HierarchicalSkillPlanner(AgentConfig(
            base_url="http://127.0.0.1:1/v1", model="fake", objective="test"),
            self.registry, client=client)
        result = planner.plan(data, "规划")
        self.assertEqual(client.roles, ["joint", "component_air"])
        calls = result["skill_calls"]
        self.assertEqual(calls[0]["call_id"], "patrol")
        self.assertEqual(calls[0]["unit_guids"], [unit_id(1)])
        self.assertEqual([len(c["unit_guids"]) for c in calls[1:]], [2, 2])
        self.assertEqual(len(validate_plan(result, data, self.registry)), 3)
        traces = [event for event in client.events if event[1] == "rule_allocation"]
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces[0][2]["output"]["original_calls"]), 1)
        self.assertIn("对海规则实际分配", result["summary"])
        self.assertEqual(result["service_proposals"]["air"]["model_summary"], "巡逻和对海打击")


if __name__ == "__main__":
    unittest.main()
