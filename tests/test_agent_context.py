"""输入隔离与实际 HTTP 请求中固定合同的位置；无网络调用。"""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from agents.model_client import AgentConfig, ChatCompletionClient
from rules.agent_context import service_missions, service_rules
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]


class AgentContextTests(unittest.TestCase):
    def test_rule_imports_in_fresh_process_without_runtime_import_cycle(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "from rules.agent_context import service_rules; assert service_rules({}, 'air') == {}"],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_planner_filters_service_inputs_but_joint_keeps_global_rules(self):
        class Client:
            def __init__(self):
                self.contexts = {}

            def plan(self, role, system, context):
                self.contexts[role] = context
                if role == "joint":
                    return {"version": 1, "mission_intents": []}
                return {"version": 1, "service": role.removeprefix("component_"),
                        "summary": "test", "skill_calls": []}

        situation = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        situation["operational_rules"] = {"awacs_trigger": {"kind": "hold"}}
        situation["existing_missions"] = [
            {"guid": "dddddddd-1111-1111-1111-111111111111", "name": "known-awacs",
             "skill_id": "air.early_warning"}]
        client = Client()
        planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake", objective="test"),
            SkillRegistry(ROOT / "skills").load(), client=client)
        planner.plan(situation, "test")
        navy = client.contexts["component_navy"]
        self.assertNotIn("awacs_trigger", navy["operational_rules"])
        self.assertNotIn("air_strike_opportunities", navy["operational_rules"])
        self.assertEqual(navy["existing_missions"], [])
        self.assertIn("awacs_trigger", client.contexts["joint"]["situation"]["operational_rules"])
        self.assertIn("scenario_time", navy["operational_rules"])

    def test_foreign_rules_removed_and_common_time_kept_without_mutating_source(self):
        rules = {"scenario_time": {"remaining_minutes": 45},
                 "awacs_trigger": {"kind": "opening"},
                 "air_strike_opportunities": {"radius_km": 1000},
                 "patrol_spacing": {"conflicts": []},
                 "navy_patrol_opening_allowed": False,
                 "future_common_rule": {"value": 1}}
        before = deepcopy(rules)
        navy = service_rules(rules, "navy")
        self.assertEqual(set(navy), {"scenario_time", "navy_patrol_opening_allowed",
                                     "future_common_rule"})
        air = service_rules(rules, "air")
        self.assertIn("air_strike_opportunities", air)
        self.assertIn("patrol_spacing", air)
        self.assertNotIn("awacs_trigger", air)
        awacs = service_rules(rules, "awacs")
        self.assertIn("awacs_trigger", awacs)
        self.assertIn("patrol_spacing", awacs)
        self.assertNotIn("air_strike_opportunities", awacs)
        navy["scenario_time"]["remaining_minutes"] = 0
        self.assertEqual(rules, before)

    def test_mission_agent_owner_takes_precedence_over_service(self):
        registry = SkillRegistry(ROOT / "skills").load()
        missions = [{"name": "cap", "skill_id": "air.combat_patrol"},
                    {"name": "awacs", "skill_id": "air.early_warning"},
                    {"name": "sea", "skill_id": "navy.sea_patrol"},
                    {"name": "external"}]
        air = service_missions(missions, "air", registry)
        self.assertEqual([row["name"] for row in air], ["cap", "external"])
        self.assertEqual([row["name"] for row in
                          service_missions(missions, "awacs", registry)],
                         ["awacs", "external"])
        air[0]["name"] = "changed"
        self.assertEqual(missions[0]["name"], "cap")

    def test_skill_contract_precedes_changing_situation_in_sent_request(self):
        client = ChatCompletionClient(AgentConfig(
            base_url="http://127.0.0.1:1/v1", model="fake", objective="test"))
        prefix = []
        with patch.object(client, "load_api_key", return_value=None):
            for role, catalog_key in (("component_air", "task_skills"),
                                       ("joint", "available_task_skills")):
                for number in (1, 2):
                    context = {"situation": {"round": number},
                               "objective": "same", catalog_key: [{"skill_id": "x"}]}
                    original = deepcopy(context)
                    body = json.dumps({"choices": [{"finish_reason": "stop",
                                                     "message": {"content": "{}"}}]}).encode()
                    with patch("agents.model_client.build_opener") as opener:
                        opener.return_value.open.return_value = BytesIO(body)
                        client._complete("system", context, json_output=True,
                                         model="fake", max_tokens=128, usage_key=role)
                        request = opener.return_value.open.call_args.args[0]
                    sent = json.loads(request.data)["messages"][1]["content"]
                    self.assertLess(sent.index(catalog_key), sent.index("situation"))
                    prefix.append(sent.split('"situation"', 1)[0])
                    self.assertEqual(context, original)
        self.assertEqual(prefix[0], prefix[1])
        self.assertEqual(prefix[2], prefix[3])


if __name__ == "__main__":
    unittest.main()
