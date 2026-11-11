import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.model_client import AgentConfig
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry
from rules.situation_memory import memory_brief, update_memory


class RecordingClient:
    def __init__(self):
        self.roles = []
        self.joint_contexts = []
        self.service_contexts = {}

    def plan(self, role, _system, context):
        self.roles.append(role)
        if role == "joint":
            self.joint_contexts.append(context)
            return {"version": 1, "mission_intents": [], "main_effort": "防御"}
        self.service_contexts[role] = context
        return {"version": 1, "service": role.removeprefix("component_"),
                "summary": "待命", "skill_calls": []}


class AgentActivationAndJointRefreshTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.situation["scenario"]["initial_time"] = "2026-11-06 10:00:00"
        self.situation["operational_rules"] = {"awacs_trigger": {"kind": "hold"}}
        self.now = 100.0
        self.client = RecordingClient()
        self.planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                        objective="test", joint_interval_sec=900,
                        component_parallelism=4),
            self.registry, client=self.client, clock=lambda: self.now)

    def plan(self, instruction="按态势规划"):
        return self.planner.plan(self.situation, instruction)

    def test_skips_unavailable_agents_but_keeps_air_patrol_capability(self):
        first = self.plan()
        self.assertEqual(first["joint_source"], "model")
        self.assertEqual(self.client.roles[0], "joint")
        self.assertCountEqual(self.client.roles[1:], ["component_air", "component_navy"])
        self.assertIn("army", first["agent_skips"])
        self.assertIn("awacs", first["agent_skips"])
        self.assertEqual(self.client.joint_contexts[0]["available_task_skills"]["army"], [])
        self.situation["contacts"] = {kind: [] for kind in self.situation["contacts"]}
        self.situation["operational_rules"]["navy_patrol_opening_allowed"] = False
        self.client.roles.clear()
        second = self.plan()
        self.assertIn("component_air", self.client.roles)
        self.assertNotIn("component_navy", self.client.roles)
        self.assertIn("navy", second["agent_skips"])

    def test_reuses_joint_until_interval_and_ignores_same_class_contact_churn(self):
        self.plan()
        self.client.roles.clear()
        self.now = 200
        self.situation["contacts"]["air"].append({
            "guid": "dddddddd-0000-0000-0000-000000000099", "lat": 23, "lon": 132})
        second = self.plan()
        self.assertEqual(second["joint_source"], "cache")
        self.assertNotIn("joint", self.client.roles)
        self.assertEqual(len(self.client.joint_contexts), 1)
        self.now = 1000
        third = self.plan()
        self.assertEqual(third["joint_source"], "model")
        self.assertEqual(third["joint_refresh_reason"], "联合方案到达刷新间隔")

    def test_strategic_changes_refresh_immediately_and_retry_reuses_joint(self):
        self.plan()
        self.now = 101
        retry = self.plan("按态势规划\n上一版任务方案校验失败：单位重复。请修正")
        self.assertEqual(retry["joint_source"], "cache")
        self.situation["contacts"]["sea"] = []
        changed = self.plan()
        self.assertEqual(changed["joint_refresh_reason"], "敌方威胁类别出现或消失")
        self.situation["units"]["navy"].pop()
        changed = self.plan()
        self.assertEqual(changed["joint_refresh_reason"], "蓝方兵力变化")
        self.situation["scenario"]["initial_time"] = "2026-11-07 10:00:00"
        changed = self.plan()
        self.assertEqual(changed["joint_refresh_reason"], "想定或推演方变化")

    def test_no_units_skips_all_service_calls(self):
        self.situation["units"] = {"air": [], "navy": [], "army": [], "submarine": []}
        plan = self.plan()
        self.assertEqual(self.client.roles, ["joint"])
        self.assertEqual(set(plan["agent_skips"]), {"air", "awacs", "navy", "army"})

    def test_joint_and_services_receive_only_bounded_relevant_memory(self):
        memory = update_memory({}, self.situation)
        self.situation["situation_memory"] = memory_brief(memory)
        self.situation["_service_situation_memory"] = {
            owner: memory_brief(memory, owner) for owner in ("air", "awacs", "navy", "army")}
        self.plan()
        joint_situation = self.client.joint_contexts[0]["situation"]
        self.assertIn("situation_memory", joint_situation)
        self.assertNotIn("_service_situation_memory", joint_situation)
        navy_memory = self.client.service_contexts["component_navy"]["situation_memory"]
        self.assertTrue(navy_memory["active_contact_trends"])
        self.assertTrue(all(item["group"] in {"sea", "air"}
                            for item in navy_memory["active_contact_trends"]))

    def test_sole_enemy_early_warning_axis_reaches_joint_and_air_agent(self):
        self.situation["contacts"]["air"] = [{
            "guid": "dddddddd-0000-0000-0000-000000000001",
            "name": "卡-29RLD预警直升机", "lat": 20, "lon": 134,
        }]
        self.plan()
        joint = self.client.joint_contexts[0]["situation"]
        axis = joint["operational_rules"]["suspected_air_attack_axis"]
        self.assertEqual(axis["status"], "suspected")
        self.assertEqual(axis["contact_guid"], self.situation["contacts"]["air"][0]["guid"])
        self.assertEqual(len(joint["contacts"]["air"]), 1)
        self.assertEqual(self.client.service_contexts["component_air"]["operational_rules"]
                         ["suspected_air_attack_axis"], axis)

    def test_air_strike_priority_rechecks_patrol_only_answer_once(self):
        class StrikeReviewClient(RecordingClient):
            def __init__(self):
                super().__init__()
                self.air_contexts = []

            def plan(self, role, system, context):
                if role != "component_air":
                    return super().plan(role, system, context)
                self.roles.append(role)
                self.air_contexts.append(context)
                if len(self.air_contexts) == 1:
                    return {"version": 1, "service": "air", "summary": "目标太远，继续巡逻",
                            "skill_calls": []}
                return {"version": 1, "service": "air", "summary": "对当前敌机出动",
                        "skill_calls": [{"call_id": "counter-air-1",
                                         "skill_id": "air.air_strike",
                                         "mission_name": "当前空中接触打击",
                                         "unit_guids": ["aaaaaaaa-0000-0000-0000-000000000001"],
                                         "target_contact_guids": [
                                             "dddddddd-0000-0000-0000-000000000001"]}]}

        self.situation["scenario"].update(
            start_time="2026-11-06 11:50:00",
            end_time="2026-11-06 13:20:00")
        self.client = StrikeReviewClient()
        self.planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                        objective="test", joint_interval_sec=900,
                        component_parallelism=4),
            self.registry, client=self.client, clock=lambda: self.now)
        plan = self.plan()
        self.assertEqual(self.client.roles.count("joint"), 1)
        self.assertEqual(len(self.client.air_contexts), 2)
        self.assertEqual(plan["service_proposals"]["air"]["air_strike_review"], {
            "triggered": True, "focus_skill_id": "air.air_strike",
            "strike_submitted": True})
        self.assertEqual(plan["skill_calls"][0]["skill_id"], "air.air_strike")
        self.assertEqual(self.client.air_contexts[1]["planning_feedback"]["rule"],
                         "air_strike_1000km_review")
        self.assertTrue(self.client.joint_contexts[0]["situation"]
                        ["operational_rules"]["air_strike_opportunities"]
                        ["eligible_by_skill"]["air.air_strike"])

    def test_time_budget_reaches_agents_and_phase_change_refreshes_joint(self):
        self.situation["scenario"].update(
            start_time="2026-11-06 10:40:00",
            end_time="2026-11-06 13:20:00",
        )
        self.plan()
        first_budget = (self.client.joint_contexts[-1]["situation"]
                        ["operational_rules"]["scenario_time"])
        self.assertEqual(first_budget["total_minutes"], 200.0)
        self.assertEqual(first_budget["remaining_minutes"], 160.0)
        self.assertEqual(first_budget["planning_priority"], "侦察部署")
        self.assertEqual(self.client.service_contexts["component_air"]
                         ["operational_rules"]["scenario_time"], first_budget)
        self.assertEqual(self.client.service_contexts["component_navy"]
                         ["operational_rules"]["scenario_time"], first_budget)

        self.now = 101  # Real-time 900-second cache has not expired.
        self.situation["scenario"]["start_time"] = "2026-11-06 11:45:00"
        changed = self.plan()
        self.assertEqual(changed["joint_source"], "model")
        self.assertEqual(changed["joint_refresh_reason"], "推演时间进入新的筹划阶段")
        self.assertEqual(self.client.joint_contexts[-1]["situation"]["operational_rules"]
                         ["scenario_time"]["planning_priority"], "打击优先")


if __name__ == "__main__":
    unittest.main()
