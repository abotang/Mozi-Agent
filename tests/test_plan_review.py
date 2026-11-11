"""Optional review failures and independent-call preservation regressions."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.model_client import AgentConfig, ModelError
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry
from rules.plan_review import merge_review_calls


class PlanReviewMergeTests(unittest.TestCase):
    def test_revised_replaces_each_supported_identity(self):
        for identity, value in (("call_id", "same-call"),
                                ("mission_name", "same-name"),
                                ("existing_mission_guid", "ABC")):
            with self.subTest(identity=identity):
                first = {"call_id": "old", "unit_guids": ["u1"], identity: value}
                revised = {"call_id": "new", "unit_guids": ["u2"], identity: value}
                if identity == "existing_mission_guid":
                    revised[identity] = "{abc}"
                merged, report = merge_review_calls([first], [revised])
                self.assertEqual(merged, [revised])
                self.assertEqual(report["replaced_call_ids"], [first["call_id"]])

    def test_main_and_escort_conflicts_prefer_revision(self):
        original = [{"call_id": "old", "unit_guids": ["{ABC}"]}]
        revised = [{"call_id": "new", "escort_unit_guids": ["abc"]}]
        merged, report = merge_review_calls(original, revised)
        self.assertEqual(merged, revised)
        self.assertEqual(report["conflicting_call_ids"], ["old"])

    def test_independent_call_is_preserved_without_mutating_inputs(self):
        old = [{"call_id": "keep", "unit_guids": ["a"]}]
        new = [{"call_id": "new", "unit_guids": ["b"]}]
        merged, report = merge_review_calls(old, new)
        self.assertEqual([x["call_id"] for x in merged], ["new", "keep"])
        self.assertEqual(report["preserved_call_ids"], ["keep"])
        merged[0]["unit_guids"].append("c")
        self.assertEqual(new[0]["unit_guids"], ["b"])


class OptionalReviewPlannerTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.situation["scenario"].update(initial_time="2026-11-06 10:00:00",
                                          start_time="2026-11-06 11:50:00",
                                          end_time="2026-11-06 13:20:00")
        self.situation["operational_rules"] = {"awacs_trigger": {"kind": "hold"}}
        self.cap = {"call_id": "keep-cap", "skill_id": "air.combat_patrol",
                    "mission_name": "Keep CAP", "unit_guids": [
                        self.situation["units"]["air"][0]["guid"]],
                    "area": {"points": [{"lat": 20, "lon": 131},
                                         {"lat": 21, "lon": 131},
                                         {"lat": 21, "lon": 132}]}}
        self.strike = {"call_id": "new-strike", "skill_id": "air.air_strike",
                       "mission_name": "New strike", "unit_guids": [
                           self.situation["units"]["air"][1]["guid"]],
                       "target_contact_guids": [
                           self.situation["contacts"]["air"][0]["guid"]]}

    def plan_with_review(self, second):
        first = deepcopy(self.cap)
        class FakeClient:
            def __init__(self):
                self.air_calls = 0
                self.events = []
            def trace_event(self, role, event, **details):
                self.events.append((role, event, details))
            def plan(self, role, prompt, context):
                if role == "joint":
                    return {"version": 1, "mission_intents": []}
                if role == "component_air":
                    self.air_calls += 1
                    if self.air_calls == 1:
                        return {"version": 1, "service": "air", "summary": "Original CAP",
                                "skill_calls": [first]}
                    if isinstance(second, Exception):
                        raise second
                    return deepcopy(second)
                return {"version": 1, "service": role.removeprefix("component_"),
                        "summary": "Other agent standby", "skill_calls": []}
        self.client = FakeClient()
        planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake", objective="test"),
            self.registry, client=self.client)
        return planner.plan(self.situation, "test")

    def test_model_and_protocol_failures_keep_first_proposal(self):
        bad_aliases = {"version": 1, "service": "air", "skill_calls": [
            {"callId": "one", "call_id": "two"}]}
        for second in (ModelError("fake timeout"), [], bad_aliases):
            with self.subTest(second=second):
                result = self.plan_with_review(second)
                air = result["service_proposals"]["air"]
                self.assertEqual(air["summary"], "Original CAP")
                self.assertEqual(air["skill_calls"][0]["call_id"], "keep-cap")
                self.assertIn("error", air["air_strike_review"])
                self.assertIn("navy", result["service_proposals"])
                self.assertEqual(self.client.air_calls, 2)
                self.assertTrue(any(event[1] == "rule_review" for event in self.client.events))

    def test_empty_review_keeps_original_calls_and_summary(self):
        result = self.plan_with_review({"version": 1, "service": "air",
                                       "summary": "No new task", "skill_calls": []})
        air = result["service_proposals"]["air"]
        self.assertEqual(air["summary"], "Original CAP")
        self.assertEqual(air["skill_calls"][0]["call_id"], "keep-cap")
        self.assertEqual(air["air_strike_review"]["revised_summary"], "No new task")

    def test_successful_review_preserves_omitted_independent_patrol(self):
        result = self.plan_with_review({"version": 1, "service": "air",
                                       "summary": "New strike", "skill_calls": [self.strike]})
        self.assertEqual([x["call_id"] for x in result["skill_calls"]],
                         ["new-strike", "keep-cap"])
        review = result["service_proposals"]["air"]["air_strike_review"]
        self.assertTrue(review["strike_submitted"])
        self.assertEqual(review["preserved_call_ids"], ["keep-cap"])

    def test_revision_wins_unit_conflict_without_duplicate_allocation(self):
        self.strike["unit_guids"] = list(self.cap["unit_guids"])
        result = self.plan_with_review({"version": 1, "service": "air",
                                       "summary": "New strike", "skill_calls": [self.strike]})
        self.assertEqual([x["call_id"] for x in result["skill_calls"]], ["new-strike"])
        review = result["service_proposals"]["air"]["air_strike_review"]
        self.assertEqual(review["conflicting_call_ids"], ["keep-cap"])


if __name__ == "__main__":
    unittest.main()
