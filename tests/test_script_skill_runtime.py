from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT),):
    if path not in sys.path:
        sys.path.insert(0, path)

from runtime.skills.compiler import PlanCompiler
from runtime.execution.executor import PowerShellMissionExecutor, ScriptExecutionError
from runtime.execution.orchestrator import ScriptSkillOrchestrator
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry
from runtime.planning.validation import validate_plan
from agents.model_client import AgentConfig, ChatCompletionClient, ModelError


def load_json(name: str):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()

    def test_seven_skills_are_owned_by_the_correct_services(self):
        self.assertEqual(len(self.registry), 7)
        self.assertEqual(len(self.registry.for_service("air")), 5)
        self.assertEqual(len(self.registry.for_service("navy")), 2)
        self.assertEqual(len(self.registry.for_service("army")), 0)
        self.assertEqual(self.registry.get("air.land_strike").service, "air")
        self.assertEqual([spec.skill_id for spec in self.registry.for_agent("awacs")],
                         ["air.early_warning"])
        self.assertEqual(len(self.registry.for_agent("air")), 4)

    def test_every_registered_script_exists(self):
        scripts = ROOT / "scripts" / "powershell"
        for skill in self.registry.all():
            self.assertTrue((scripts / skill.script).is_file(), skill.skill_id)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = load_json("situation.example.json")
        self.plan = load_json("plan.multiple.example.json")

    def test_multiple_calls_of_the_same_skill_are_allowed(self):
        calls = validate_plan(self.plan, self.situation, self.registry)
        self.assertEqual(len(calls), 4)
        self.assertEqual([item.skill_id for item in calls[:2]],
                         ["air.combat_patrol", "air.combat_patrol"])

    def test_patrol_without_area_is_rejected(self):
        plan = deepcopy(self.plan)
        plan["skill_calls"][0]["area"] = None
        with self.assertRaisesRegex(ValueError, "必须划定区域"):
            validate_plan(plan, self.situation, self.registry)

    def test_ship_patrol_route_point_list_is_compiled(self):
        call = deepcopy(self.plan["skill_calls"][0])
        call.update({
            "call_id": "ship-patrol-list-route",
            "skill_id": "navy.sea_patrol",
            "mission_name": "蓝方舰艇巡逻",
            "unit_guids": ["bbbbbbbb-0000-0000-0000-000000000001"],
            "routes": {"outbound": [
                {"lat": 20.0, "lon": 130.0},
                {"lat": 21.0, "lon": 131.0},
            ], "on_station": []},
        })
        calls = validate_plan({"version": 1, "skill_calls": [call]},
                              self.situation, self.registry)
        self.assertEqual(len(calls[0].routes["outbound"]["points"]), 2)
        with TemporaryDirectory(dir=ROOT) as folder:
            invocation = PlanCompiler(self.registry, ROOT / "scripts" / "powershell",
                                      folder).compile(calls, self.situation, "route-list")[0]
        self.assertEqual(len(invocation.config["tasks"]["shipSeaPatrol"]["routes"]["outbound"]["points"]), 2)

    def test_invalid_ship_route_reports_field(self):
        call = deepcopy(self.plan["skill_calls"][0])
        call.update({
            "call_id": "ship-patrol-invalid-route",
            "skill_id": "navy.sea_patrol",
            "mission_name": "蓝方舰艇巡逻",
            "unit_guids": ["bbbbbbbb-0000-0000-0000-000000000001"],
            "routes": {"outbound": "bad"},
        })
        with self.assertRaisesRegex(ValueError, "routes.outbound 必须是航线对象"):
            validate_plan({"version": 1, "skill_calls": [call]},
                          self.situation, self.registry)

    def test_submarine_cannot_be_assigned_to_navy_skill(self):
        situation = deepcopy(self.situation)
        submarine_guid = "bbbbbbbb-0000-0000-0000-000000000099"
        situation["units"]["navy"].append({
            "guid": submarine_guid, "name": "潜艇-1", "unit_type": "Submarine",
        })
        call = deepcopy(self.plan["skill_calls"][0])
        call.update({"skill_id": "navy.sea_patrol", "unit_guids": [submarine_guid]})
        with self.assertRaisesRegex(ValueError, "单元类型 Submarine 不属于 navy 军种"):
            validate_plan({"version": 1, "skill_calls": [call]}, situation, self.registry)


class HierarchicalPlannerTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = load_json("situation.example.json")
        self.plan = load_json("plan.multiple.example.json")

    class FakeClient:
        def plan(self, role, system, context):
            if role == "joint":
                return {"version": 1, "situation_assessment": "当前有海空接触",
                        "main_effort": "建立防空并压制敌舰",
                        "mission_intents": [
                            {"service": "air", "objective": "建立防空巡逻"},
                            {"service": "navy", "objective": "攻击已探测敌舰"},
                        ], "coordination": []}
            if role == "component_air":
                return {"version": 1, "service": "air", "summary": "建立CAP",
                        "skill_calls": [{
                            "call_id": "air-cap", "skill_id": "air.combat_patrol",
                            "mission_name": "空军CAP", "unit_guids": [
                                "aaaaaaaa-0000-0000-0000-000000000001"],
                            "escort_unit_guids": [], "target_contact_guids": [],
                            "loadout_guid": "", "existing_mission_guid": "",
                            "area": {"points": [
                                {"lat": 21.5, "lon": 130}, {"lat": 21.5, "lon": 131},
                                {"lat": 22.5, "lon": 131}]}, "reason": "北部防空"}]}
            if role == "component_awacs":
                return {"version": 1, "service": "awacs", "summary": "预警机待命",
                        "skill_calls": []}
            if role == "component_navy":
                return {"version": 1, "service": "navy", "summary": "对海攻击",
                        "skill_calls": [{
                            "call_id": "navy-strike", "skill_id": "navy.sea_strike",
                            "mission_name": "舰艇对海", "unit_guids": [
                                "bbbbbbbb-0000-0000-0000-000000000001"],
                            "escort_unit_guids": [], "target_contact_guids": [
                                "eeeeeeee-0000-0000-0000-000000000001"],
                            "loadout_guid": "", "existing_mission_guid": "",
                            "area": None, "routes": None, "reason": "当前敌舰接触"}]}
            return {"version": 1, "service": "army", "summary": "当前无陆军脚本",
                    "skill_calls": []}

    def test_joint_and_four_components_produce_only_registered_skill_calls(self):
        registry = SkillRegistry(ROOT / "skills").load()
        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试")
        planner = HierarchicalSkillPlanner(config, registry, client=self.FakeClient())
        situation = load_json("situation.example.json")
        plan = planner.plan(situation, "按态势规划")
        calls = validate_plan(plan, situation, registry)
        self.assertEqual([item.skill_id for item in calls],
                         ["air.combat_patrol", "navy.sea_strike"])
        self.assertEqual(plan["service_proposals"]["army"]["skill_calls"], [])
        self.assertEqual(plan["service_proposals"]["awacs"]["skill_calls"], [])

    def test_awacs_agent_gets_only_awacs_skill_and_air_agent_cannot_take_aircraft(self):
        class CapturingClient(self.FakeClient):
            contexts = {}

            def plan(self, role, system, context):
                self.contexts[role] = context
                if role == "component_awacs":
                    return {"version": 1, "service": "awacs", "summary": "机场预警",
                            "skill_calls": [{"call_id": "awacs-only", "skill_id": "air.early_warning",
                                             "mission_name": "机场预警", "unit_guids": [
                                                 "aaaaaaaa-0000-0000-0000-000000000004"],
                                             "area": {"pattern": "racetrack"}}]}
                return super().plan(role, system, context)

        situation = deepcopy(self.situation)
        situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方机场", "unit_type": "Facility", "lat": 20.3, "lon": 130.3})
        client = CapturingClient()
        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试", component_parallelism=4)
        plan = HierarchicalSkillPlanner(config, self.registry, client=client).plan(
            situation, "按态势规划")
        self.assertEqual([item["skill_id"] for item in
                          client.contexts["component_air"]["task_skills"]],
                         ["air.air_strike", "air.combat_patrol", "air.land_strike", "air.sea_strike"])
        self.assertEqual([item["skill_id"] for item in
                          client.contexts["component_awacs"]["task_skills"]],
                         ["air.early_warning"])
        self.assertEqual([item["skill_id"] for item in
                          client.contexts["joint"]["available_task_skills"]["awacs"]],
                         ["air.early_warning"])
        awacs_guid = "aaaaaaaa-0000-0000-0000-000000000004"
        self.assertNotIn(awacs_guid, [row["guid"] for row in
                                      client.contexts["component_air"]["own_units"]])
        self.assertEqual([row["guid"] for row in
                          client.contexts["component_awacs"]["own_units"]], [awacs_guid])
        self.assertEqual(len(validate_plan(plan, situation, self.registry)), 3)

    def test_air_agent_cannot_call_awacs_owned_skill(self):
        class CrossingClient(self.FakeClient):
            def plan(self, role, system, context):
                if role == "component_air":
                    return {"version": 1, "service": "air", "summary": "越权",
                            "skill_calls": [{"call_id": "cross", "skill_id": "air.early_warning",
                                             "mission_name": "越权预警", "unit_guids": [
                                                 "aaaaaaaa-0000-0000-0000-000000000004"],
                                             "area": {"pattern": "racetrack"}}]}
                return super().plan(role, system, context)

        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试")
        with self.assertRaisesRegex(ValueError, "air Agent 越权调用了 awacs Skill"):
            HierarchicalSkillPlanner(config, self.registry, client=CrossingClient()).plan(
                self.situation, "按态势规划")

    def test_no_awacs_skips_awacs_model_request(self):
        class CountingClient(self.FakeClient):
            roles = []

            def plan(self, role, system, context):
                self.roles.append(role)
                return super().plan(role, system, context)

        situation = deepcopy(self.situation)
        situation["units"]["air"] = [row for row in situation["units"]["air"]
                                     if "预警" not in row["name"]]
        client = CountingClient()
        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试")
        plan = HierarchicalSkillPlanner(config, self.registry, client=client).plan(
            situation, "按态势规划")
        self.assertNotIn("component_awacs", client.roles)
        self.assertEqual(plan["service_proposals"]["awacs"]["skill_calls"], [])

    def test_planner_normalizes_agent_aliases_before_skill_lookup(self):
        class AliasClient(self.FakeClient):
            def plan(self, role, system, context):
                result = super().plan(role, system, context)
                if role == "component_air":
                    call = result["skill_calls"][0]
                    call["skillId"] = call.pop("skill_id")
                    call["unitGuids"] = call.pop("unit_guids")[0]
                return result

        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试")
        planner = HierarchicalSkillPlanner(config, self.registry, client=AliasClient())
        plan = planner.plan(self.situation, "按态势规划")
        self.assertEqual(plan["skill_calls"][0]["skill_id"], "air.combat_patrol")
        self.assertEqual(len(validate_plan(plan, self.situation, self.registry)), 2)

    def test_later_navy_catalog_excludes_ship_patrol(self):
        class CapturingClient(self.FakeClient):
            contexts = {}

            def plan(self, role, system, context):
                self.contexts[role] = context
                return super().plan(role, system, context)

        client = CapturingClient()
        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                             objective="测试")
        situation = deepcopy(self.situation)
        situation["operational_rules"] = {"navy_patrol_opening_allowed": False}
        HierarchicalSkillPlanner(config, self.registry, client=client).plan(
            situation, "按态势规划")
        navy_skills = [item["skill_id"] for item in client.contexts["component_navy"]["task_skills"]]
        self.assertEqual(navy_skills, ["navy.sea_strike"])
        joint_navy = [item["skill_id"] for item in
                      client.contexts["joint"]["available_task_skills"]["navy"]]
        self.assertEqual(joint_navy, navy_skills)

    def test_strike_without_current_contact_is_rejected(self):
        plan = deepcopy(self.plan)
        plan["skill_calls"][2]["target_contact_guids"] = [
            "99999999-9999-9999-9999-999999999999"
        ]
        with self.assertRaisesRegex(ValueError, "不是蓝方当前接触"):
            validate_plan(plan, self.situation, self.registry)

    def test_air_skill_cannot_use_navy_unit(self):
        plan = deepcopy(self.plan)
        plan["skill_calls"][0]["unit_guids"] = [
            "bbbbbbbb-0000-0000-0000-000000000002"
        ]
        with self.assertRaisesRegex(ValueError, "不属于 air"):
            validate_plan(plan, self.situation, self.registry)

    def test_same_unit_cannot_be_reused_across_calls(self):
        plan = deepcopy(self.plan)
        plan["skill_calls"][1]["unit_guids"] = list(plan["skill_calls"][0]["unit_guids"])
        with self.assertRaisesRegex(ValueError, "重复分配"):
            validate_plan(plan, self.situation, self.registry)

    def test_malformed_skill_call_reports_service_and_position(self):
        with self.assertRaisesRegex(ValueError, "navy Agent skill_calls 第 1 项必须是 JSON 对象"):
            HierarchicalSkillPlanner._component(
                {"version": 1, "service": "navy", "skill_calls": [[]]}, "navy")


class ModelResponseTests(unittest.TestCase):
    def test_usage_details_array_is_ignored(self):
        usage = ChatCompletionClient._normalized_usage({
            "usage": {"prompt_tokens": 100, "prompt_tokens_details": []}})
        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["cached_tokens"], 0)

    def test_model_envelope_array_has_clear_error(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b"[]"

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake", objective="测试")
        client = ChatCompletionClient(config)
        with patch("agents.model_client.build_opener", return_value=Opener()):
            with self.assertRaisesRegex(ModelError, "顶层必须是 JSON 对象"):
                client._complete("", {}, json_output=True, model="fake",
                                 max_tokens=128, usage_key="joint")


class CompilerAndExecutorTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry(ROOT / "skills").load()
        self.situation = load_json("situation.example.json")
        self.plan = load_json("plan.multiple.example.json")

    def test_each_skill_call_compiles_to_an_independent_config(self):
        calls = validate_plan(self.plan, self.situation, self.registry)
        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            invocations = compiler.compile(calls, self.situation, run_id="test")
            self.assertEqual(len(invocations), 4)
            first = invocations[0]
            enabled = [key for key, value in first.config["tasks"].items() if value["enabled"]]
            self.assertEqual(enabled, ["airCombatPatrol"])
            self.assertEqual(first.config["tasks"]["airCombatPatrol"]["area"]["typeId"], "405")
            self.assertEqual(invocations[2].config["tasks"]["airSeaStrike"]
                             ["targetContactGuids"],
                             ["eeeeeeee-0000-0000-0000-000000000001"])

    def test_executor_validates_all_calls_before_execute(self):
        calls = validate_plan(self.plan, self.situation, self.registry)
        recorded = []

        def runner(command, **kwargs):
            recorded.append(command)
            if "-ValidateOnly" in command:
                return subprocess.CompletedProcess(command, 0, stdout='{"valid":true}', stderr="")
            config_path = Path(command[command.index("-ConfigPath") + 1])
            report_path = Path(json.loads(config_path.read_text(encoding="utf-8"))["reportPath"])
            report_path.write_text("{}", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                "success": True, "tasks": [{"missionGuid": "11111111-2222-3333-4444-555555555555"}]
            }), stderr="")

        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            invocations = compiler.compile(calls, self.situation, run_id="test")
            executor = PowerShellMissionExecutor(executable="pwsh", runner=runner)
            results = executor.execute(invocations, dry_run=False)
        self.assertEqual(len(results), 8)
        self.assertTrue(all("-ValidateOnly" in command for command in recorded[:4]))
        self.assertTrue(all("-ValidateOnly" not in command for command in recorded[4:]))
        self.assertTrue(all(isinstance(command, list) for command in recorded))

    def test_zero_exit_with_powershell_parser_error_is_failure(self):
        calls = validate_plan({"version": 1, "skill_calls": self.plan["skill_calls"][:1]},
                              self.situation, self.registry)
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="ParserError")
        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            invocation = compiler.compile(calls, self.situation, run_id="parser-error")
            executor = PowerShellMissionExecutor(executable="pwsh", runner=runner)
            with self.assertRaisesRegex(ScriptExecutionError, "ParserError"):
                executor.execute(invocation, dry_run=True)

    def test_powershell_7_validates_utf8_script(self):
        executable = shutil.which("pwsh")
        if not executable:
            self.skipTest("PowerShell 7 unavailable")
        calls = validate_plan({"version": 1, "skill_calls": self.plan["skill_calls"][:1]},
                              self.situation, self.registry)
        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            invocation = compiler.compile(calls, self.situation, run_id="powershell-7")
            result = PowerShellMissionExecutor(executable=executable).execute(
                invocation, dry_run=True)
        self.assertTrue(result[0].success, result[0].stderr)

    def test_orchestrator_dry_run_never_executes_creation_phase(self):
        recorded = []

        def runner(command, **kwargs):
            recorded.append(command)
            return subprocess.CompletedProcess(command, 0, stdout='{"valid":true}', stderr="")

        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            executor = PowerShellMissionExecutor(executable="pwsh", runner=runner)
            orchestrator = ScriptSkillOrchestrator(self.registry, compiler, executor)
            result = orchestrator.execute_plan(self.plan, self.situation,
                                               dry_run=True, run_id="dry")
        self.assertEqual(result["skill_call_count"], 4)
        self.assertEqual(len(recorded), 4)
        self.assertTrue(all("-ValidateOnly" in command for command in recorded))

    def test_all_seven_real_powershell_entries_accept_compiled_config(self):
        executable = shutil.which("pwsh")
        if not executable:
            self.skipTest("PowerShell unavailable")
        air = "aaaaaaaa-0000-0000-0000-000000000001"
        ship = "bbbbbbbb-0000-0000-0000-000000000001"
        targets = {
            "Aircraft": "dddddddd-0000-0000-0000-000000000001",
            "Ship": "eeeeeeee-0000-0000-0000-000000000001",
            "Facility": "ffffffff-0000-0000-0000-000000000001",
        }
        area = {"points": [
            {"lat": 20.0, "lon": 130.0},
            {"lat": 20.0, "lon": 131.0},
            {"lat": 21.0, "lon": 131.0},
            {"lat": 21.0, "lon": 130.0},
        ]}
        self.situation["units"]["army"].append({
            "guid": "cccccccc-0000-0000-0000-000000000002",
            "name": "蓝方机场", "unit_type": "Facility", "lat": 20.3, "lon": 130.3})
        with TemporaryDirectory(dir=ROOT) as folder:
            compiler = PlanCompiler(self.registry, ROOT / "scripts" / "powershell", folder)
            executor = PowerShellMissionExecutor(executable=executable)
            for sequence, spec in enumerate(self.registry.all(), 1):
                call = {
                    "call_id": f"all-{sequence}",
                    "skill_id": spec.skill_id,
                    "mission_name": f"合同校验-{sequence}",
                    "unit_guids": [
                        "aaaaaaaa-0000-0000-0000-000000000004"
                        if spec.skill_id == "air.early_warning" else
                        air if spec.service == "air" else ship],
                    "escort_unit_guids": [],
                    "target_contact_guids": ([targets[spec.target_kind]]
                                               if spec.requires_target else []),
                    "loadout_guid": "",
                    "existing_mission_guid": "",
                    "area": ({"pattern": "racetrack"}
                             if spec.skill_id == "air.early_warning" else
                             deepcopy(area) if spec.requires_area else None),
                    "routes": None,
                }
                if spec.skill_id == "air.sea_strike":
                    call["unit_guids"].append("aaaaaaaa-0000-0000-0000-000000000090")
                plan = {"version": 1, "skill_calls": [call]}
                calls = validate_plan(plan, self.situation, self.registry)
                invocation = compiler.compile(calls, self.situation,
                                              run_id=f"contract-{sequence}")
                result = executor.execute(invocation, dry_run=True)
                self.assertTrue(result[0].success, spec.skill_id)


if __name__ == "__main__":
    unittest.main()
