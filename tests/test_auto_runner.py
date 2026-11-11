import json
from io import BytesIO
from itertools import count
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.skills.compiler import PlanCompiler
from runtime.contracts import ExecutionResult
from runtime.app.daemon import AutomaticMissionRunner, _mission_exists
from runtime.execution.executor import ScriptExecutionError
from runtime.skills.registry import SkillRegistry


class AutoRunnerTests(unittest.TestCase):
    def test_mission_business_error_preserves_deduplication(self):
        for payload in ({"code": 101, "data": None, "message": "接口暂时不可用"},
                        {"data": None}, {"code": 0}, []):
            with self.subTest(payload=payload), patch(
                    "runtime.app.daemon.urlopen",
                    return_value=BytesIO(json.dumps(payload).encode("utf-8"))):
                self.assertTrue(_mission_exists("http://invalid/api", "existing-mission"))

    def test_mission_success_response_can_confirm_absence(self):
        for data, expected in ((None, False), ({}, False), ({"guid": "existing"}, True)):
            with self.subTest(data=data), patch(
                    "runtime.app.daemon.urlopen",
                    return_value=BytesIO(json.dumps({"code": 0, "data": data}).encode("utf-8"))):
                self.assertEqual(_mission_exists("http://invalid/api", "existing-mission"), expected)

    def test_failed_planning_retries_unchanged_situation_next_poll(self):
        self._assert_failed_proposal_retries(fail_validation=False)

    def test_failed_validation_retries_unchanged_situation_next_poll(self):
        self._assert_failed_proposal_retries(fail_validation=True)

    def _assert_failed_proposal_retries(self, *, fail_validation):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        # Keep AWACS on hold so its opening trigger cannot mask retry gating.
        situation["units"]["air"] = [row for row in situation["units"]["air"]
                                     if "预警" not in row["name"]]
        invalid = json.loads((ROOT / "examples/plan.multiple.example.json").read_text(encoding="utf-8"))
        invalid["skill_calls"] = invalid["skill_calls"][:1]
        invalid["skill_calls"][0]["area"] = None

        class Planner:
            calls = 0

            def plan(self, *_):
                self.calls += 1
                if self.calls == 1:
                    if fail_validation:
                        return invalid
                    raise RuntimeError("temporary model failure")
                return {"version": 1, "summary": "观察", "skill_calls": []}

        planner = Planner()
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)), planner=planner,
                registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=object(), state_path=Path(folder) / "state.json",
                sleep=lambda _: None, clock=lambda: 100.0, output=lambda _: None)
            runner.run("观察", max_cycles=2)
        self.assertEqual(planner.calls, 2)

    def test_duplicate_unit_gets_one_immediate_replan(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples/situation.example.json")
                               .read_text(encoding="utf-8"))
        source = json.loads((ROOT / "examples/plan.multiple.example.json")
                            .read_text(encoding="utf-8"))
        valid = json.loads(json.dumps(source["skill_calls"][:2]))
        duplicate = json.loads(json.dumps(valid))
        duplicate[1]["unit_guids"] = list(duplicate[0]["unit_guids"])

        class Planner:
            requests = []

            def plan(self, _situation, instruction):
                self.requests.append(instruction)
                return {"version": 1, "summary": "test", "skill_calls":
                        duplicate if len(self.requests) == 1 else valid}

        class Executor:
            created = []

            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    return []
                item = invocations[0]
                self.created.append(item.call.mission_name)
                guid = f"11111111-2222-3333-4444-{len(self.created):012d}"
                return [ExecutionResult(item.call.call_id, item.call.skill_id,
                                        "execute", True, 0,
                                        json.dumps({"tasks": [{"missionGuid": guid}]}), "",
                                        str(item.config_path), str(item.report_path))]

        planner, executor, lines = Planner(), Executor(), []
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)),
                planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=executor, state_path=Path(folder) / "state.json",
                sleep=lambda _: None, output=lines.append)
            runner.run("自动测试", max_cycles=1)
        self.assertEqual(len(planner.requests), 2)
        self.assertIn("重复分配", planner.requests[1])
        self.assertEqual(len(executor.created), 2)
        self.assertIn("立即请 Agent 重规划一次", "\n".join(lines))

    def test_navy_patrol_is_opening_only_but_later_strike_runs(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples" / "situation.example.json")
                               .read_text(encoding="utf-8"))
        example = json.loads((ROOT / "examples" / "plan.multiple.example.json")
                             .read_text(encoding="utf-8"))
        patrol = json.loads(json.dumps(example["skill_calls"][0]))
        patrol.update({"call_id": "ship-opening", "skill_id": "navy.sea_patrol",
                       "mission_name": "开局海上巡逻",
                       "unit_guids": ["bbbbbbbb-0000-0000-0000-000000000001"]})
        later_patrol = json.loads(json.dumps(patrol))
        later_patrol.update({"call_id": "ship-later", "mission_name": "后续海上巡逻"})
        strike = json.loads(json.dumps(example["skill_calls"][3]))
        strike.update({"call_id": "ship-strike", "mission_name": "舰艇对海打击",
                       "unit_guids": ["bbbbbbbb-0000-0000-0000-000000000002"]})

        class Planner:
            flags = []

            def plan(self, current, _instruction):
                self.flags.append(current["operational_rules"]["navy_patrol_opening_allowed"])
                calls = [patrol] if len(self.flags) == 1 else [later_patrol, strike]
                return {"version": 1, "skill_calls": calls, "summary": "测试"}

        class Executor:
            created = []

            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    return []
                item = invocations[0]
                self.created.append(item.call.skill_id)
                guid = f"11111111-2222-3333-4444-{len(self.created):012d}"
                return [ExecutionResult(item.call.call_id, item.call.skill_id,
                                        "execute", True, 0,
                                        json.dumps({"tasks": [{"missionGuid": guid}]}), "",
                                        str(item.config_path), str(item.report_path))]

        planner, executor = Planner(), Executor()
        timer = count(400, 400)
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)),
                planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", folder),
                executor=executor, state_path=Path(folder) / "state.json",
                sleep=lambda _: None, clock=lambda: next(timer), output=lambda _: None)
            with patch("runtime.app.daemon._mission_exists", return_value=True):
                runner.run("自动测试", max_cycles=2)
            state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(planner.flags, [True, False])
        self.assertCountEqual(executor.created, ["navy.sea_patrol", "navy.sea_strike"])
        self.assertTrue(state["navy_patrol_opening_done"])
        self.assertNotIn("后续海上巡逻", state["missions"])

    def test_current_time_change_keeps_recovered_mission_state(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples" / "situation.example.json")
                               .read_text(encoding="utf-8"))
        situation["scenario"]["initial_time"] = "2026-11-06 08:00:00"
        mission_guid = "11111111-2222-3333-4444-555555555555"
        with TemporaryDirectory(dir=ROOT) as folder:
            reports = Path(folder) / "old-run" / "reports"
            reports.mkdir(parents=True)
            (reports / "one.json").write_text(json.dumps({
                "side": situation["side"],
                "tasks": [{"key": "airCombatPatrol", "name": "已有CAP",
                           "missionGuid": mission_guid}],
            }), encoding="utf-8")
            runner = AutomaticMissionRunner(
                collector=lambda: situation, planner=object(), registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", folder),
                executor=object(), state_path=Path(folder) / "state.json")
            with patch("runtime.app.daemon._mission_exists", return_value=True):
                runner._reconcile(situation)
                self.assertIn("已有CAP", runner.state["missions"])
                situation["scenario"]["start_time"] = "2026-11-06 09:00:00"
                runner._reconcile(situation)
            self.assertIn("已有CAP", runner.state["missions"])
            self.assertIn("2026-11-06 08:00:00", runner.state["scenario"])

    def test_uncertain_execution_is_not_retried_after_restart(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples" / "situation.example.json")
                               .read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples" / "plan.multiple.example.json")
                          .read_text(encoding="utf-8"))
        plan["skill_calls"] = plan["skill_calls"][:1]

        class Planner:
            def plan(self, _situation, _instruction):
                return plan

        class Executor:
            executions = 0

            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    return []
                self.executions += 1
                item = invocations[0]
                result = ExecutionResult(item.call.call_id, item.call.skill_id,
                                         "execute", False, 0, "", "ParserError",
                                         str(item.config_path), str(item.report_path))
                raise ScriptExecutionError("ParserError", [result])

        executor = Executor()
        with TemporaryDirectory(dir=ROOT) as folder:
            state_path = Path(folder) / "state.json"
            for _ in range(2):
                runner = AutomaticMissionRunner(
                    collector=lambda: json.loads(json.dumps(situation)),
                    planner=Planner(), registry=registry,
                    compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", folder),
                    executor=executor, state_path=state_path,
                    sleep=lambda _: None, output=lambda _: None)
                runner.run("自动测试", max_cycles=1)
            state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(executor.executions, 1)
        self.assertIn("蓝方-北区战斗巡逻", state["uncertain"])
        self.assertEqual(state["situation_memory"]["outcomes"][-1]["status"], "uncertain")

    def test_shows_agent_plan_before_validation_error(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples" / "situation.example.json")
                               .read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples" / "plan.multiple.example.json")
                          .read_text(encoding="utf-8"))
        plan["skill_calls"] = plan["skill_calls"][:1]
        plan["skill_calls"][0]["area"] = None

        class Planner:
            def plan(self, _situation, _instruction):
                return plan

        lines = []
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)),
                planner=Planner(), registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", folder),
                executor=object(), state_path=Path(folder) / "state.json",
                sleep=lambda _: None, output=lines.append)
            runner.run("自动测试", max_cycles=1)
        transcript = "\n".join(lines)
        self.assertIn("Agent 完整方案", transcript)
        self.assertIn("蓝方-北区战斗巡逻", transcript)
        self.assertIn('"area": null', transcript)
        self.assertLess(transcript.index("Agent 完整方案"),
                        transcript.index("转换并校验任务参数失败"))

    def test_creates_once_then_skips_unchanged_situation(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples" / "situation.example.json").read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples" / "plan.multiple.example.json").read_text(encoding="utf-8"))
        plan["skill_calls"] = plan["skill_calls"][:1]

        class Planner:
            calls = 0

            def plan(self, situation, instruction):
                self.calls += 1
                return plan

        class Executor:
            validations = 0
            executions = 0

            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    self.validations += 1
                    return []
                self.executions += 1
                item = invocations[0]
                return [ExecutionResult(
                    item.call.call_id, item.call.skill_id, "execute", True, 0,
                    json.dumps({"tasks": [{"missionGuid": "11111111-2222-3333-4444-555555555555"}]}),
                    "", str(item.config_path), str(item.report_path))]

        planner = Planner()
        executor = Executor()
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)),
                planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts" / "powershell", folder),
                executor=executor, state_path=Path(folder) / "state.json",
                sleep=lambda _: None, clock=lambda: 100.0, output=lambda _: None)
            with patch("runtime.app.daemon._mission_exists", return_value=True):
                runner.run("自动测试", max_cycles=2)
            state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(planner.calls, 1)
        self.assertEqual(executor.executions, 1)
        self.assertEqual(executor.validations, 1)
        self.assertIn("蓝方-北区战斗巡逻", state["missions"])
        self.assertEqual(state["situation_memory"]["outcomes"][-1]["status"], "created")

    def test_independent_tasks_execute_concurrently_after_full_preflight(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples/plan.multiple.example.json").read_text(encoding="utf-8"))
        plan["skill_calls"] = plan["skill_calls"][:2]
        barrier = Barrier(2, timeout=3)

        class Planner:
            def plan(self, *_):
                return plan

        class Executor:
            preflight = False
            started = []

            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    self.preflight = True
                    return []
                assert self.preflight
                item = invocations[0]
                self.started.append(item.call.call_id)
                barrier.wait()
                suffix = 1 if item.call.call_id == "cap-north" else 2
                return [ExecutionResult(item.call.call_id, item.call.skill_id,
                                        "execute", True, 0,
                                        json.dumps({"tasks": [{"missionGuid":
                                            f"11111111-2222-3333-4444-{suffix:012d}"}]}),
                                        "", str(item.config_path), str(item.report_path))]

        executor = Executor()
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)), planner=Planner(),
                registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=executor, state_path=Path(folder) / "state.json",
                script_parallelism=2, sleep=lambda _: None, output=lambda _: None)
            runner.run("自动测试", max_cycles=1)
            state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        self.assertCountEqual(executor.started, ["cap-north", "cap-south"])
        self.assertIn("蓝方-北区战斗巡逻", state["missions"])
        self.assertIn("蓝方-南区战斗巡逻", state["missions"])

    def test_one_parallel_failure_does_not_discard_other_success(self):
        registry = SkillRegistry(ROOT / "skills").load()
        situation = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples/plan.multiple.example.json").read_text(encoding="utf-8"))
        plan["skill_calls"] = plan["skill_calls"][:2]
        barrier = Barrier(2, timeout=3)

        class Planner:
            def plan(self, *_):
                return plan

        class Executor:
            def execute(self, invocations, *, dry_run=False):
                if dry_run:
                    return []
                item = invocations[0]
                barrier.wait()
                if item.call.call_id == "cap-north":
                    result = ExecutionResult(item.call.call_id, item.call.skill_id,
                                             "execute", False, 1, "", "API失败",
                                             str(item.config_path), str(item.report_path))
                    raise ScriptExecutionError("API失败", [result])
                return [ExecutionResult(item.call.call_id, item.call.skill_id,
                                        "execute", True, 0,
                                        json.dumps({"tasks": [{"missionGuid":
                                            "11111111-2222-3333-4444-000000000002"}]}),
                                        "", str(item.config_path), str(item.report_path))]

        lines = []
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=lambda: json.loads(json.dumps(situation)), planner=Planner(),
                registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=Executor(), state_path=Path(folder) / "state.json",
                script_parallelism=2, sleep=lambda _: None, output=lines.append)
            runner.run("自动测试", max_cycles=1)
            state = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
        self.assertIn("蓝方-南区战斗巡逻", state["missions"])
        self.assertIn("蓝方-北区战斗巡逻", state["uncertain"])
        self.assertIn("1 项任务执行失败", "\n".join(lines))

    def test_contact_movement_wakes_agent_and_memory_survives_restart(self):
        registry = SkillRegistry(ROOT / "skills").load()
        template = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        template["units"]["air"] = [row for row in template["units"]["air"]
                                     if "预警" not in row["name"]]
        template["scenario"]["initial_time"] = "2026-06-05 08:00:00"
        now = [0]
        counter = [0]

        def collect():
            index = counter[0]
            counter[0] += 1
            now[0] = index * 10
            current = json.loads(json.dumps(template))
            current["scenario"]["start_time"] = f"2026-06-05 08:{index:02d}:00"
            current["contacts"]["sea"][0]["lon"] = 140 if index == 0 else 139.5
            return current

        class Planner:
            memories = []

            def plan(self, situation, _instruction):
                self.memories.append(situation["situation_memory"])
                return {"version": 1, "summary": "观察态势", "skill_calls": []}

        planner = Planner()
        lines = []
        with TemporaryDirectory(dir=ROOT) as folder:
            state_path = Path(folder) / "state.json"
            runner = AutomaticMissionRunner(
                collector=collect, planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=object(), state_path=state_path, sleep=lambda _: None,
                clock=lambda: now[0], output=lines.append)
            runner.run("观察", max_cycles=5)
            recovered = AutomaticMissionRunner(
                collector=collect, planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=object(), state_path=state_path)
        self.assertEqual(len(planner.memories), 2)
        self.assertIn("移动", "\n".join(lines))
        self.assertTrue(any(item["approach_km"] > 0 for item in
                            planner.memories[-1]["active_contact_trends"]
                            if item["group"] == "sea"))
        self.assertTrue(recovered.state["situation_memory"]["tracks"])

    def test_simulation_rewind_clears_joint_cache_and_forces_replan(self):
        registry = SkillRegistry(ROOT / "skills").load()
        template = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        template["units"]["air"] = [row for row in template["units"]["air"]
                                     if "预警" not in row["name"]]
        template["scenario"]["initial_time"] = "2026-06-05 08:00:00"
        counter = [0]

        def collect():
            current = json.loads(json.dumps(template))
            current["scenario"]["start_time"] = (
                "2026-06-05 08:05:00" if counter[0] == 0 else "2026-06-05 08:00:00")
            counter[0] += 1
            return current

        class Planner:
            calls = 0
            resets = 0

            def plan(self, *_):
                self.calls += 1
                return {"version": 1, "summary": "观察", "skill_calls": []}

            def reset_cached_directive(self):
                self.resets += 1

        planner = Planner()
        with TemporaryDirectory(dir=ROOT) as folder:
            runner = AutomaticMissionRunner(
                collector=collect, planner=planner, registry=registry,
                compiler=PlanCompiler(registry, ROOT / "scripts/powershell", folder),
                executor=object(), state_path=Path(folder) / "state.json",
                sleep=lambda _: None, clock=lambda: 0, output=lambda _: None)
            runner.run("观察", max_cycles=2)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(planner.resets, 1)


if __name__ == "__main__":
    unittest.main()
