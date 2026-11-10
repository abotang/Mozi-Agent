"""Already tasked aircraft stay unavailable for newly created missions."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents.model_client import AgentConfig
from runtime.app.daemon import AutomaticMissionRunner
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.planning.validation import validate_plan
from runtime.skills.compiler import PlanCompiler
from runtime.skills.registry import SkillRegistry
from rules.air_patrol_update import patrol_update_options, validate_air_call


ROOT = Path(__file__).resolve().parents[1]
FIGHTER = "aaaaaaaa-0000-0000-0000-000000000001"
ESCORT = "aaaaaaaa-0000-0000-0000-000000000002"
OLD_MISSION = "11111111-2222-4333-8444-555555555555"


class RecordingClient:
    def __init__(self):
        self.air_context = None

    def plan(self, role, _system, context):
        if role == "joint":
            return {"version": 1, "mission_intents": []}
        owner = role.removeprefix("component_")
        if owner == "air":
            self.air_context = context
        return {"version": 1, "service": owner, "summary": "", "skill_calls": []}


class AircraftOccupancyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SkillRegistry(ROOT / "skills").load()

    def setUp(self):
        self.situation = json.loads((ROOT / "examples/situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.call = json.loads((ROOT / "examples/plan.multiple.example.json")
                               .read_text(encoding="utf-8"))["skill_calls"][0]
        self.situation["mission_assignments"] = {FIGHTER: OLD_MISSION}

    def test_occupied_aircraft_hidden_from_air_agent(self):
        client = RecordingClient()
        planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                        objective="test", component_parallelism=1),
            self.registry, client=client)
        planner.plan(self.situation, "test")
        self.assertNotIn(FIGHTER, {unit["guid"] for unit in client.air_context["own_units"]})
        self.assertIn(ESCORT, {unit["guid"] for unit in client.air_context["own_units"]})

    def test_owned_patrol_aircraft_available_only_for_original_area_update(self):
        self.situation["existing_missions"] = [{
            "name": self.call["mission_name"], "guid": OLD_MISSION,
            "skill_id": "air.combat_patrol", "current_area": self.call["area"]}]
        client = RecordingClient()
        planner = HierarchicalSkillPlanner(
            AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                        objective="test", component_parallelism=1),
            self.registry, client=client)
        planner.plan(self.situation, "test")
        self.assertIn(FIGHTER, {unit["guid"] for unit in client.air_context["own_units"]})
        options = client.air_context["patrol_updates"]
        self.assertEqual(options[0]["unit_guids"], [FIGHTER])
        self.assertEqual(options[0]["current_area"], self.call["area"])
        update = deepcopy(self.call)
        update["existing_mission_guid"] = OLD_MISSION
        validate_air_call(update, options)
        with self.assertRaisesRegex(ValueError, "不能创建新任务"):
            validate_air_call(self.call, options)
        update["skill_id"] = "air.air_strike"
        with self.assertRaisesRegex(ValueError, "保留原任务"):
            validate_air_call(update, options)

    def test_patrol_update_must_keep_whole_assigned_group(self):
        self.situation["mission_assignments"][ESCORT] = OLD_MISSION
        self.situation["existing_missions"] = [{
            "name": self.call["mission_name"], "guid": OLD_MISSION,
            "skill_id": "air.combat_patrol"}]
        options = patrol_update_options(self.situation)
        self.assertEqual(options[0]["unit_guids"], [FIGHTER, ESCORT])
        update = deepcopy(self.call)
        update["existing_mission_guid"] = OLD_MISSION
        with self.assertRaisesRegex(ValueError, "全部已分配飞机"):
            validate_air_call(update, options)

    def test_new_task_rejected_but_same_mission_update_allowed(self):
        plan = {"version": 1, "skill_calls": [deepcopy(self.call)]}
        with self.assertRaisesRegex(ValueError, "飞机已被任务占用"):
            validate_plan(plan, self.situation, self.registry)
        plan["skill_calls"][0]["existing_mission_guid"] = OLD_MISSION
        self.assertEqual(1, len(validate_plan(plan, self.situation, self.registry)))

    def test_occupied_escort_rejected(self):
        strike = json.loads((ROOT / "examples/plan.multiple.example.json")
                            .read_text(encoding="utf-8"))["skill_calls"][2]
        strike["escort_unit_guids"] = [FIGHTER]
        with self.assertRaisesRegex(ValueError, "飞机已被任务占用"):
            validate_plan({"version": 1, "skill_calls": [strike]},
                          self.situation, self.registry)

    def test_automatic_runner_rechecks_before_script(self):
        situation = deepcopy(self.situation)
        situation["mission_assignments"] = {}
        plan = {"version": 1, "skill_calls": [deepcopy(self.call)]}

        class Planner:
            def plan(self, _situation, _instruction):
                return plan

        class Executor:
            called = False

            def execute(self, _invocations, *, dry_run=False):
                self.called = True
                return []

        executor, output = Executor(), []
        with TemporaryDirectory(dir=ROOT) as folder, patch(
                "runtime.app.daemon.read_blue_mission_assignments",
                return_value={FIGHTER: OLD_MISSION}):
            runner = AutomaticMissionRunner(
                collector=lambda: deepcopy(situation), planner=Planner(),
                registry=self.registry,
                compiler=PlanCompiler(self.registry, ROOT / "scripts/powershell", folder),
                executor=executor, state_path=Path(folder) / "state.json",
                sleep=lambda _seconds: None, output=output.append)
            runner.run("test", max_cycles=1)
        self.assertFalse(executor.called)
        self.assertIn("飞机已被任务占用", "\n".join(output))


if __name__ == "__main__":
    unittest.main()
