"""Evidence and sequencing gates for aviation sea-strike authorization."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from agents.model_client import AgentConfig
from rules.campaign_phase import (evaluate_phase, observation, phase_context,
                                  suppress_sea_calls, validate_phase_calls)
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


FIGHTER = "aaaaaaaa-0000-0000-0000-000000000001"
MISSION = "bbbbbbbb-0000-0000-0000-000000000001"
CONTACT = "cccccccc-0000-0000-0000-000000000001"


def situation(airborne=True, assigned=True, name="F-16A #1"):
    return {"scenario": {"start_time": "2026-11-11 10:05:00"},
            "units": {"air": [{"guid": FIGHTER, "name": name,
                               "unit_type": "Aircraft", "airborne": airborne}]},
            "mission_assignments": {FIGHTER: MISSION} if assigned else {},
            "contacts": {"air": [{"guid": CONTACT}], "sea": []},
            "operational_rules": {}}


def directive(status="established", evidence=None, phase="sea_strike"):
    return {"operation_phase": phase, "air_control_assessment": {
        "status": status, "evidence": ["F-16在空执行前出巡逻，目标方向未见新增空中争夺"]
        if evidence is None else evidence}}


class CampaignPhaseTests(unittest.TestCase):
    def test_joint_prompt_example_contains_valid_phase_assessment_contract(self):
        prompt = (Path(__file__).resolve().parents[1] / "prompts/joint.md").read_text(encoding="utf-8")
        example = json.loads(prompt.split("只输出 JSON：")[-1].strip())
        self.assertIn(example["operation_phase"], ("reconnaissance", "air_control", "sea_strike"))
        self.assertIn(example["air_control_assessment"]["status"],
                      ("not_established", "contested", "established"))
        self.assertIsInstance(example["air_control_assessment"]["evidence"], list)

    def planner_case(self, *, airborne=False, prior="reconnaissance", include_patrol=True):
        data = situation(airborne=airborne, assigned=airborne)
        data["side"] = {"name": "蓝方", "guid": MISSION}
        data["scenario"] = {"initial_time": "2026-11-11 10:00:00",
                            "start_time": "2026-11-11 10:05:00", "end_time": "2026-11-11 13:20:00"}
        base = data["units"]["air"][0]
        base.update(lat=0., lon=0., strike_capabilities=["air", "sea"], weapon_info_status="ok")
        unit2 = "aaaaaaaa-0000-0000-0000-000000000002"
        unit3 = "aaaaaaaa-0000-0000-0000-000000000003"
        data["units"]["air"].extend([{**base, "guid": guid, "airborne": False} for guid in (unit2, unit3)])
        data["units"].update(navy=[], army=[], submarine=[])
        data["contacts"] = {"air": [], "land": [], "submarine": [], "sea": [
            {"guid": CONTACT, "name": "敌舰", "unit_type": "Ship", "lat": 0., "lon": 1.}]}
        data.update(existing_missions=[], existing_areas=[])
        data["operational_rules"] = {"campaign_phase": phase_context({"phase": prior}, data),
                                     "awacs_trigger": {"kind": "hold"}}
        patrol = {"call_id": "patrol", "skill_id": "air.combat_patrol", "mission_name": "巡逻",
                  "unit_guids": [FIGHTER], "area": {"points": [
                      {"lat": 0., "lon": 0.}, {"lat": 0., "lon": .2},
                      {"lat": .2, "lon": .2}, {"lat": .2, "lon": 0.}]}}
        sea = {"call_id": "sea", "skill_id": "air.sea_strike", "mission_name": "对海",
               "unit_guids": [unit2, unit3], "target_contact_guids": [CONTACT]}

        class Client:
            def __init__(self):
                self.roles, self.air_contexts, self.events = [], [], []
                self.assessment = directive()

            def plan(self, role, system, context):
                self.roles.append(role)
                if role == "joint":
                    return {"version": 1, "mission_intents": [], **deepcopy(self.assessment)}
                if role == "component_air":
                    self.air_contexts.append(deepcopy(context))
                    return {"version": 1, "service": "air", "summary": "请求巡逻和对海",
                            "skill_calls": ([deepcopy(patrol)] if include_patrol else []) + [deepcopy(sea)]}
                raise AssertionError(f"unexpected model call {role}")

            def trace_event(self, role, event, **values):
                self.events.append((role, event, values))

        registry = SkillRegistry(Path(__file__).resolve().parents[1] / "skills").load()
        client = Client()
        planner = HierarchicalSkillPlanner(AgentConfig(base_url="http://127.0.0.1:1/v1",
            model="fake", objective="test", joint_interval_sec=3600), registry,
            client=client, clock=lambda: 100.)
        return data, client, planner, registry

    def test_context_starts_in_recon_and_prior_permission_is_not_reused(self):
        data = situation()
        self.assertEqual(phase_context({}, data)["phase"], "reconnaissance")
        context = phase_context({"phase": "sea_strike", "sea_strike_allowed": True,
                                 "assessment": {"status": "established"}}, data)
        self.assertFalse(context["sea_strike_allowed"])
        self.assertEqual(context["previous_assessment"]["status"], "established")

    def test_cannot_jump_recon_to_sea_even_with_positive_assessment(self):
        data = situation()
        result = evaluate_phase(phase_context({}, data), directive(), data)
        self.assertEqual(result["phase"], "air_control")
        self.assertFalse(result["sea_strike_allowed"])

    def test_next_assessment_with_current_airborne_force_can_open_sea_phase(self):
        data = situation()
        first = evaluate_phase(phase_context({}, data), directive(), data)
        second = evaluate_phase(phase_context(first, data), directive(), data)
        self.assertEqual(second["phase"], "sea_strike")
        self.assertTrue(second["sea_strike_allowed"])
        self.assertEqual(second["observation"]["airborne_assigned_fighter_guids"], [FIGHTER])

    def test_grounded_force_cannot_establish_control_despite_model_claim(self):
        data = situation(airborne=False)
        # Regression: the scenario had eighteen aircraft still on the ground.
        data["units"]["air"] = [{**data["units"]["air"][0],
                                   "guid": f"aaaaaaaa-0000-0000-0000-{i:012d}"}
                                  for i in range(1, 19)]
        data["mission_assignments"] = {u["guid"]: MISSION for u in data["units"]["air"]}
        result = evaluate_phase(phase_context({"phase": "air_control"}, data), directive(), data)
        self.assertFalse(result["sea_strike_allowed"])
        self.assertIn("尚无实际在空", result["reason"])

    def test_unassigned_aircraft_awacs_and_jammer_cannot_supply_fighter_evidence(self):
        for data in (situation(assigned=False), situation(name="E-2D预警机"),
                     situation(name="EC-130H电子干扰机"), situation(airborne="true")):
            with self.subTest(unit=data["units"]["air"][0]):
                result = evaluate_phase(phase_context({"phase": "air_control"}, data), directive(), data)
                self.assertFalse(result["sea_strike_allowed"])

    def test_contested_or_missing_current_assessment_relocks_previous_sea_phase(self):
        data = situation()
        context = phase_context({"phase": "sea_strike"}, data)
        for proposal in (directive(status="contested"), directive(status="not_established"),
                         directive(evidence=[]), directive(evidence=[" ", None, 1]),
                         {"operation_phase": "sea_strike"}, {}):
            with self.subTest(proposal=proposal):
                result = evaluate_phase(context, proposal, data)
                self.assertEqual(result["phase"], "air_control")
                self.assertFalse(result["sea_strike_allowed"])

    def test_contact_disappearance_and_time_alone_do_not_authorize_sea_strike(self):
        data = situation()
        data["contacts"]["air"] = []
        data["scenario"]["start_time"] = "2026-11-11 13:59:59"
        data["operational_rules"]["scenario_time"] = {"planning_priority": "strike", "remaining_minutes": 1}
        result = evaluate_phase(phase_context({"phase": "air_control"}, data),
                                {"operation_phase": "sea_strike"}, data)
        self.assertFalse(result["sea_strike_allowed"])

    def test_explicit_regression_to_recon_relocks(self):
        data = situation()
        result = evaluate_phase(phase_context({"phase": "sea_strike"}, data),
                                directive(phase="reconnaissance"), data)
        self.assertEqual(result["phase"], "reconnaissance")
        self.assertFalse(result["sea_strike_allowed"])

    def test_suppression_preserves_air_combat_and_patrol_and_does_not_mutate(self):
        calls = [{"skill_id": "air.combat_patrol", "unit_guids": ["patrol"]},
                 {"skill_id": "air.air_strike", "unit_guids": ["intercept"]},
                 {"skill_id": "air.sea_strike", "unit_guids": ["strike1", "strike2"]}]
        before = deepcopy(calls)
        kept, rejected = suppress_sea_calls(calls, {"enabled": True, "sea_strike_allowed": False})
        self.assertEqual(kept, calls[:2])
        self.assertEqual(rejected, calls[2:])
        kept[0]["unit_guids"].clear()
        self.assertEqual(calls, before)

    def test_legacy_disabled_context_keeps_all_calls(self):
        data = situation(airborne=False)
        result = evaluate_phase(phase_context({}, data, enabled=False), {}, data)
        self.assertTrue(result["sea_strike_allowed"])
        calls = [{"skill_id": "air.sea_strike"}]
        self.assertEqual(suppress_sea_calls(calls, result), (calls, []))

    def test_validation_recomputes_and_rejects_forged_permission_flag(self):
        data = situation(airborne=False)
        data["operational_rules"]["campaign_phase"] = phase_context({"phase": "air_control"}, data)
        plan = {"joint_directive": directive(),
                "campaign_phase": {"enabled": True, "phase": "sea_strike", "sea_strike_allowed": True}}
        with self.assertRaisesRegex(ValueError, "未获阶段许可"):
            validate_phase_calls([SimpleNamespace(skill_id="air.sea_strike")], plan, data)
        validate_phase_calls([SimpleNamespace(skill_id="air.air_strike")], plan, data)

    def test_validation_allows_current_assessment_and_airborne_assigned_fighter(self):
        data = situation()
        data["operational_rules"]["campaign_phase"] = phase_context({"phase": "air_control"}, data)
        validate_phase_calls([SimpleNamespace(skill_id="air.sea_strike")],
                             {"joint_directive": directive()}, data)

    def test_observation_guid_normalization_and_unknown_airborne(self):
        data = situation()
        data["mission_assignments"] = {"{" + FIGHTER.upper() + "}": MISSION}
        self.assertEqual(observation(data)["airborne_assigned_fighter_guids"], [FIGHTER])
        data["units"]["air"][0].pop("airborne")
        self.assertEqual(observation(data)["airborne_assigned_fighter_guids"], [])

    def test_planner_suppresses_premature_sea_call_but_keeps_patrol_and_catalog_filtered(self):
        data, client, planner, registry = self.planner_case()
        result = planner.plan(data, "根据态势推进")
        self.assertEqual(client.roles, ["joint", "component_air"])
        self.assertFalse(result["campaign_phase"]["sea_strike_allowed"])
        self.assertEqual([c["skill_id"] for c in result["skill_calls"]], ["air.combat_patrol"])
        self.assertNotIn("air.sea_strike", {s["skill_id"] for s in client.air_contexts[0]["task_skills"]})
        self.assertEqual(len(validate_plan(result, data, registry)), 1)
        self.assertEqual(len(result["service_proposals"]["air"]["phase_suppressed_calls"]), 1)
        self.assertTrue(any(event[1] == "phase_gate" for event in client.events))

    def test_planner_opens_sea_after_prior_control_and_current_assessment(self):
        data, client, planner, registry = self.planner_case(airborne=True, prior="air_control", include_patrol=False)
        result = planner.plan(data, "根据态势推进")
        self.assertTrue(result["campaign_phase"]["sea_strike_allowed"])
        self.assertIn("air.sea_strike", {s["skill_id"] for s in client.air_contexts[0]["task_skills"]})
        self.assertEqual([c["skill_id"] for c in result["skill_calls"]], ["air.sea_strike"])
        self.assertEqual(len(result["skill_calls"][0]["unit_guids"]), 2)
        self.assertEqual(len(validate_plan(result, data, registry)), 1)
        self.assertEqual(client.roles, ["joint", "component_air"])

    def test_airborne_observation_change_refreshes_joint_and_relocks_even_before_interval(self):
        data, client, planner, registry = self.planner_case(airborne=True, prior="air_control", include_patrol=False)
        first = planner.plan(data, "根据态势推进")
        self.assertTrue(first["campaign_phase"]["sea_strike_allowed"])
        data["units"]["air"][0]["airborne"] = False
        second = planner.plan(data, "根据态势推进")
        self.assertEqual(client.roles.count("joint"), 2)
        self.assertEqual(second["joint_source"], "model")
        self.assertFalse(second["campaign_phase"]["sea_strike_allowed"])
        self.assertEqual(second["skill_calls"], [])


if __name__ == "__main__":
    unittest.main()
