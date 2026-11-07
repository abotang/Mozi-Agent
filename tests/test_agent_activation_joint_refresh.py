import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.model_client import AgentConfig
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry


class RecordingClient:
    def __init__(self):
        self.roles = []
        self.joint_contexts = []

    def plan(self, role, _system, context):
        self.roles.append(role)
        if role == "joint":
            self.joint_contexts.append(context)
            return {"version": 1, "mission_intents": [], "main_effort": "防御"}
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
            "guid": "dddddddd-0000-0000-0000-000000000099", "lat": 21, "lon": 132})
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


if __name__ == "__main__":
    unittest.main()
