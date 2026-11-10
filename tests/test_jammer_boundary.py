"""Air Agent and direct-plan boundaries around rule-owned jammer aircraft."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from agents.model_client import AgentConfig
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]
JAMMER = "aaaaaaaa-0000-0000-0000-000000000099"
FIGHTER_1 = "aaaaaaaa-0000-0000-0000-000000000001"
FIGHTER_2 = "aaaaaaaa-0000-0000-0000-000000000002"
TARGET = "dddddddd-0000-0000-0000-000000000001"


def air_strike(*, main=FIGHTER_1, escorts=()):
    return {"version": 1, "skill_calls": [{
        "call_id": "strike-1", "skill_id": "air.air_strike",
        "mission_name": "测试对空打击", "unit_guids": [main],
        "escort_unit_guids": list(escorts), "target_contact_guids": [TARGET],
    }]}


class FakeClient:
    def __init__(self, air_call=None):
        self.air_call = air_call
        self.air_context = None

    def plan(self, role, _system, context):
        if role == "joint":
            return {"version": 1, "situation_assessment": "test",
                    "main_effort": "test", "mission_intents": [], "coordination": []}
        owner = role.removeprefix("component_")
        if owner == "air":
            self.air_context = context
        return {"version": 1, "service": owner, "summary": "test",
                "skill_calls": [deepcopy(self.air_call)] if owner == "air" and self.air_call else []}


class JammerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SkillRegistry(ROOT / "skills").load()

    def setUp(self):
        self.situation = json.loads((ROOT / "examples" / "situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.situation["units"]["air"].append({
            "guid": JAMMER, "name": "EC-130H 电子干扰机", "sub_type": "EC-130H",
            "unit_type": "Aircraft", "lat": 20.0, "lon": 130.0,
        })
        self.situation["operational_rules"] = {"awacs_trigger": {"kind": "hold"}}

    def test_air_agent_does_not_see_jammer_and_cannot_use_it_as_escort(self):
        client = FakeClient(air_strike(escorts=[JAMMER])["skill_calls"][0])
        planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                        objective="test", component_parallelism=1),
            self.registry, client=client)
        with self.assertRaisesRegex(ValueError, "不属于 own_units 的护航单元"):
            planner.plan(self.situation, "test")
        self.assertNotIn(JAMMER, {row["guid"] for row in client.air_context["own_units"]})

    def test_direct_plan_rejects_jammer_as_main_or_escort(self):
        with self.assertRaisesRegex(ValueError, "不能作为.*主任务单元"):
            validate_plan(air_strike(main=JAMMER), self.situation, self.registry)
        with self.assertRaisesRegex(ValueError, "不能通过护航字段分配"):
            validate_plan(air_strike(escorts=[JAMMER]), self.situation, self.registry)

    def test_normal_fighter_escort_remains_valid(self):
        calls = validate_plan(air_strike(escorts=[FIGHTER_2]), self.situation,
                              self.registry)
        self.assertEqual((FIGHTER_2,), calls[0].escort_unit_guids)


if __name__ == "__main__":
    unittest.main()
