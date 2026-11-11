"""自动推演主循环：态势 → 联合/军种 Agent → Skill → 固定脚本。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
from pathlib import Path
import time
import traceback
from urllib.parse import quote
from urllib.request import urlopen

from runtime.skills.compiler import PlanCompiler
from runtime.execution.area_cleanup import OwnedAreaGarbageCollector
from runtime.execution.jammer_controller import JammerEscortController
from runtime.execution.executor import PowerShellMissionExecutor, ScriptExecutionError
from runtime.mozi.mission_assignments import read_blue_mission_assignments
from runtime.mozi.mission_catalog import read_blue_missions
from runtime.planning.validation import validate_aircraft_availability, validate_plan
from rules.awacs_deployment import enrich_existing_mission, minimum_separation_km
from rules.awacs_triggers import next_trigger, opening_signature, record_success
from rules.awacs_recovery import resolve_uncertain_awacs
from rules.navy_patrol import opening_allowed, suppress_late_calls
from rules.situation_memory import (memory_brief, record_dispatch_outcome,
                                    record_plan, scenario_rewound, update_memory)
from rules.situation_refresh import contact_snapshot, tactical_refresh_reason
from rules.side_doctrine import opening_engagement_needed
from rules.scenario_timeline import scenario_timeline


def _signature(situation: dict) -> str:
    """Unit/contact changes and stable simulation-time slots wake planning."""
    values = {
        "side": situation["side"]["guid"],
        "units": {kind: sorted(str(item["guid"]) for item in rows)
                  for kind, rows in situation["units"].items()},
        "contacts": {kind: sorted(str(item["guid"]) for item in rows)
                     for kind, rows in situation["contacts"].items()},
        "occupied_aircraft": sorted((situation.get("mission_assignments") or {}).items()),
        "air_patrol_areas": sorted(
            (str(area.get("guid", "")),
             tuple(sorted(str(guid) for guid in area.get("mission_guids", []))),
             tuple((round(float(point["lat"]), 5), round(float(point["lon"]), 5))
                   for point in area.get("points", [])))
            for area in situation.get("existing_areas", [])
        ),
        "scenario_time_slot": scenario_timeline(situation.get("scenario"))["planning_slot"],
    }
    return json.dumps(values, sort_keys=True, ensure_ascii=False)


def _mission_exists(api_base: str, mission_guid: str) -> bool:
    try:
        with urlopen(f"{api_base.rstrip('/')}/Mission?Guid={quote(mission_guid)}",
                     timeout=5) as response:
            data = json.load(response)
        # A failed business response cannot prove the task was deleted.
        # Preserve deduplication until a successful query confirms absence.
        if not isinstance(data, dict):
            return True
        # Mozi explicitly reports a deleted mission as code 101, not code 0.
        # Only this exact response proves absence; other business errors keep
        # the deduplication record until the next successful query.
        if (data.get("code") == 101 and data.get("message") == "任务不存在"
                and "data" in data and data["data"] is None):
            return False
        if data.get("code") != 0 or "data" not in data:
            return True
        return bool(data.get("data"))
    except Exception:
        # 无法确认时维持去重，避免 API 暂时故障导致重复创建。
        return True


def _mission_guid(result) -> str:
    for item in reversed(result):
        if item.phase != "execute" or not item.success:
            continue
        try:
            tasks = json.loads(item.stdout).get("tasks", [])
            if tasks:
                return str(tasks[0].get("missionGuid") or "")
        except (ValueError, TypeError, AttributeError):
            pass
    return ""


class AutomaticMissionRunner:
    def __init__(self, *, collector, planner, registry, compiler: PlanCompiler,
                 executor: PowerShellMissionExecutor, state_path: Path,
                 poll_interval_sec: float = 10, replan_interval_sec: float = 300,
                 script_parallelism: int = 2,
                 sleep=time.sleep, clock=time.monotonic, output=print,
                 area_cleaner: OwnedAreaGarbageCollector | None = None,
                 jammer_controller: JammerEscortController | None = None,
                 opening_doctrine=None):
        self.collector = collector
        self.planner = planner
        self.registry = registry
        self.compiler = compiler
        self.executor = executor
        self.state_path = Path(state_path)
        self.poll_interval_sec = poll_interval_sec
        self.replan_interval_sec = replan_interval_sec
        if type(script_parallelism) is not int or not 1 <= script_parallelism <= 4:
            raise ValueError("script_parallelism 必须是 1 到 4 的整数")
        self.script_parallelism = script_parallelism
        self.sleep = sleep
        self.clock = clock
        self.output = output
        self.area_cleaner = area_cleaner
        self.jammer_controller = jammer_controller
        self.opening_doctrine = opening_doctrine
        self.state = self._load_state()

    def _update_jammer(self, situation: dict, cycle: int) -> None:
        if self.jammer_controller is None:
            return
        try:
            messages = self.jammer_controller.tick(situation, self.state)
            if messages:
                self._save_state()
                for message in messages:
                    self.output(f"[第 {cycle} 轮] 电子干扰伴随：{message}")
        except Exception as exc:
            # A support-controller fault must not stop strike planning.
            self.output(f"[第 {cycle} 轮] 电子干扰伴随失败：{type(exc).__name__}: {exc}")

    def _load_state(self) -> dict:
        if self.state_path.is_file():
            try:
                value = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and isinstance(value.get("missions"), dict):
                    if not isinstance(value.get("uncertain"), dict):
                        value["uncertain"] = {}
                    if not isinstance(value.get("navy_patrol_opening_done"), bool):
                        value["navy_patrol_opening_done"] = bool(value["missions"] or value["uncertain"])
                    value.setdefault("awacs_flights", {})
                    value.setdefault("situation_memory", {})
                    return value
            except (OSError, ValueError):
                pass
        return {"scenario": "", "missions": {}, "uncertain": {},
                "navy_patrol_opening_done": False, "awacs_flights": {},
                "situation_memory": {}}

    def _save_state(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(self.state_path)

    def _reconcile(self, situation: dict):
        scenario = "|".join((str(situation["side"]["guid"]),
                             str(situation["scenario"].get("initial_time")
                                 or situation["scenario"]["start_time"]),
                             str(situation["scenario"].get("name", ""))))
        if self.state["scenario"] != scenario:
            self.state = {"scenario": scenario, "missions": {}, "uncertain": {},
                          "navy_patrol_opening_done": False, "awacs_flights": {},
                          "situation_memory": {}}
            self._recover_missions(situation)
            self.state["navy_patrol_opening_done"] = bool(self.state["missions"])
            self._save_state()
        for name, record in list(self.state["missions"].items()):
            if record.get("guid") and not _mission_exists(situation["api_base"], record["guid"]):
                del self.state["missions"][name]
        self._reconcile_uncertain_missions(situation)
        self._reconcile_uncertain_awacs(situation)
        self._save_state()

    def _reconcile_uncertain_missions(self, situation: dict):
        """Unlock an old failed creation only after a live absence check.

        Existing AWACS uncertainty has its own stricter recovery rule.
        """
        # Only a verified manual deletion report authorizes release of an old
        # uncertainty lock. A missing task alone must not unlock arbitrary
        # interrupted executions (which may have other partial side effects).
        deleted_names = set()
        for path in self.state_path.parent.glob("cleanup-failed-*.json"):
            try:
                audit = json.loads(path.read_text(encoding="utf-8-sig"))
                if audit.get("success") is not True or audit.get("missionDeleted") is not True:
                    continue
                guid = str(audit["missionGuid"]).strip("{}").lower()
                rows = audit["responses"]["missionsBefore"]["data"]
                deleted_names.update(str(row["name"]) for row in rows
                                     if str(row.get("id", "")).strip("{}").lower() == guid)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        pending = {name: record for name, record in self.state.get("uncertain", {}).items()
                   if name in deleted_names and isinstance(record, dict)
                   and record.get("skill_id") != "air.early_warning"}
        if not pending:
            return
        try:
            live = read_blue_missions(situation["api_base"], situation["side"]["guid"])
        except Exception:
            return
        names = {mission["name"] for mission in live}
        occupied = {str(guid).strip("{}").lower()
                    for guid in situation.get("mission_assignments", {})}
        for name, record in pending.items():
            units = record.get("unit_guids")
            if (name not in names and isinstance(units, list)
                    and not any(str(guid).strip("{}").lower() in occupied for guid in units)):
                self.state["uncertain"].pop(name, None)
                self.output(f"已核对并解除失败任务锁：{name}；当前墨子无同名任务且原单元空闲")

    def _reconcile_uncertain_awacs(self, situation: dict):
        """Release a stale AWACS lock only after a successful live task audit."""
        if not self.state.get("uncertain_awacs"):
            return
        try:
            missions = read_blue_missions(situation["api_base"], situation["side"]["guid"])
            reason = resolve_uncertain_awacs(self.state, situation, missions)
        except Exception as exc:
            reason = f"蓝方任务列表核对失败，保持阻断：{type(exc).__name__}"
        if reason == "cleared":
            self.output("预警任务历史执行结果已核对：墨子中不存在该任务，解除阻断并重新唤醒预警机 Agent")
        elif self.state.get("awacs_uncertain_reason") != reason:
            self.state["awacs_uncertain_reason"] = reason
            self.output(f"预警任务暂缓重试：{reason}")

    def _recover_missions(self, situation: dict):
        """旧版用当前时间作想定 ID；从本项目报告恢复仍存在的任务。"""
        run_root = self.compiler.run_root
        skill_by_task = {spec.task_key: spec.skill_id for spec in self.registry.all()}
        side_guid = str(situation["side"]["guid"]).strip().strip("{}").lower()
        for path in sorted(run_root.glob("*/reports/*.json")):
            if path.name.endswith(".area.json"):
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(report, dict):
                continue
            side = report.get("side")
            if (not isinstance(side, dict) or
                    str(side.get("guid", "")).strip().strip("{}").lower() != side_guid):
                continue
            tasks = report.get("tasks")
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                name, guid = str(task.get("name", "")), str(task.get("missionGuid", ""))
                skill_id = skill_by_task.get(str(task.get("key", "")))
                if name and guid and skill_id and _mission_exists(situation["api_base"], guid):
                    self.state["missions"][name] = {
                        "guid": guid, "skill_id": skill_id, "report": str(path),
                    }

    def _cleanup_areas(self, situation: dict, cycle: int):
        if self.area_cleaner is None:
            return
        try:
            result = self.area_cleaner.collect(situation)
            if result["deleted"] or result["errors"]:
                self.output(f"[第 {cycle} 轮] 区域清理：删除 {len(result['deleted'])}，"
                            f"保留关联任务 {len(result['linked'])}，"
                            f"错误 {len(result['errors'])}")
                for error in result["errors"]:
                    self.output(f"区域清理失败：{error}")
        except Exception as exc:
            self.output(f"[第 {cycle} 轮] 区域清理失败：{type(exc).__name__}: {exc}")

    def _mark_uncertain(self, item, reason: str):
        """执行可能已经修改想定时，持久化阻止同名任务自动重试。"""
        self.state.setdefault("uncertain", {})[item.call.mission_name] = {
            "call_id": item.call.call_id,
            "skill_id": item.call.skill_id,
            "unit_guids": list(item.call.unit_guids),
            "config": str(item.config_path),
            "report": str(item.report_path),
            "reason": reason[:400],
        }
        if item.call.skill_id == "air.early_warning":
            self.state["uncertain_awacs"] = True
        self._save_state()

    def run(self, instruction: str, *, max_cycles: int | None = None):
        last_signature = None
        last_planned_at = -float("inf")
        last_planned_contacts = None
        cycles = 0
        self.output("自动推演已启动；按 Ctrl+C 停止。")
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            started = self.clock()
            stage = "读取态势"
            try:
                situation = self.collector()
                stage = "核对已有任务"
                self._reconcile(situation)
                rewound = scenario_rewound(self.state.get("situation_memory"), situation)
                if rewound:
                    self.state["jammer_support"] = {}
                    reset_directive = getattr(self.planner, "reset_cached_directive", None)
                    if callable(reset_directive):
                        reset_directive()
                    last_signature = None
                    last_planned_contacts = None
                    last_planned_at = -float("inf")
                    self.output(f"[第 {cycles} 轮] 想定时间倒退：清空上一局态势记忆与联合方案缓存")
                if (self.opening_doctrine is not None
                        and opening_engagement_needed(self.state, rewound=rewound)):
                    stage = "设置蓝方开局接战条令"
                    setting = self.opening_doctrine(situation)
                    self.state["opportunity_targets_scenario"] = self.state["scenario"]
                    self._save_state()
                    self.output(f"[第 {cycles} 轮] 蓝方接战临机目标：是"
                                f"（{'已设置' if setting.get('changed') else '原已启用'}）")
                self._update_jammer(situation, cycles)
                # 每次轮询先归纳态势；即使本轮不调用模型，轨迹也连续保存。
                memory = update_memory(self.state.get("situation_memory"), situation)
                self.state["situation_memory"] = memory
                situation["situation_memory"] = memory_brief(memory)
                situation["_service_situation_memory"] = {
                    owner: memory_brief(memory, owner)
                    for owner in ("air", "awacs", "navy", "army")
                }
                situation["existing_missions"] = [
                    enrich_existing_mission(name, record)
                    for name, record in self.state["missions"].items()
                ]
                awacs_trigger = next_trigger(situation, self.state)
                self.state["last_awacs_trigger"] = awacs_trigger
                self._save_state()
                navy_patrol_allowed = opening_allowed(self.state)
                situation["operational_rules"] = {
                    "navy_patrol_opening_allowed": navy_patrol_allowed,
                    "awacs_min_separation_km": minimum_separation_km(situation),
                    "awacs_trigger": awacs_trigger,
                    "scenario_time": scenario_timeline(situation.get("scenario")),
                }
                signature = _signature(situation)
                current_contacts = contact_snapshot(situation)
                tactical_reason = tactical_refresh_reason(
                    last_planned_contacts, current_contacts, started - last_planned_at)
                if (signature == last_signature
                         and started - last_planned_at < self.replan_interval_sec
                         and awacs_trigger["kind"] == "hold" and not tactical_reason):
                    self.sleep(self.poll_interval_sec)
                    continue
                if tactical_reason:
                    self.output(f"[第 {cycles} 轮] 态势变化触发重评：{tactical_reason}")
                self.output(f"[第 {cycles} 轮] 蓝方态势：飞机 {len(situation['units']['air'])}，"
                            f"海军 {len(situation['units']['navy'])}，"
                            f"潜艇 {len(situation['units'].get('submarine', []))}（暂未接入），"
                            f"接触 {sum(len(rows) for rows in situation['contacts'].values())}")
                timeline = situation["operational_rules"]["scenario_time"]
                if timeline["total_minutes"] is not None:
                    self.output(f"[第 {cycles} 轮] 推演时间：总 {timeline['total_minutes']} 分钟，"
                                f"已过 {timeline['elapsed_minutes']} 分钟，"
                                f"剩余 {timeline['remaining_minutes']} 分钟；"
                                f"当前优先：{timeline['planning_priority']}")
                for attempt in range(2):
                    stage = "联合指挥与军种 Agent 规划"
                    set_monitor_context = getattr(self.planner, "set_monitor_context", None)
                    if callable(set_monitor_context):
                        set_monitor_context(scenario_key=self.state["scenario"],
                                            scenario_name=situation["scenario"].get("name", ""),
                                            cycle=cycles, attempt=attempt + 1)
                    request = instruction
                    if attempt:
                        request += ("\n上一版任务方案校验失败：" + conflict_message
                                    + "。请按错误修正参数；一个单位在本批全部任务中只能出现一次。"
                                    "预警任务只用真实预警机，area 填 pattern=racetrack，"
                                    "有可信轴向时同时填 attack_bearing_deg 与 forward_km。不要编造 GUID。")
                    plan = self.planner.plan(situation, request)
                    if plan.get("joint_source"):
                        source = "重新调用模型" if plan["joint_source"] == "model" else "沿用上次方案"
                        detail = plan.get("joint_refresh_reason", "")
                        self.output(f"[第 {cycles} 轮] 联合指挥：{source}"
                                    + (f"（{detail}）" if detail else ""))
                    for owner, reason in plan.get("agent_skips", {}).items():
                        self.output(f"[第 {cycles} 轮] 跳过 {owner} Agent：{reason}")
                    # 校验前展示方案，便于看见被规则拒绝的单位、目标与区域。
                    display = ({key: plan[key] for key in
                                ("summary", "joint_directive", "service_proposals")
                                if key in plan}
                               if "service_proposals" in plan else plan)
                    self.output(f"[第 {cycles} 轮] Agent 完整方案"
                                + ("（冲突重规划）" if attempt else "") + ":\n"
                                + json.dumps(display, ensure_ascii=False, indent=2))
                    if not navy_patrol_allowed:
                        proposed = plan.get("skill_calls", [])
                        kept, suppressed_count = suppress_late_calls(proposed, navy_patrol_allowed)
                        if suppressed_count:
                            plan = dict(plan)
                            plan["skill_calls"] = kept
                            self.output(f"[第 {cycles} 轮] 已拦截 {suppressed_count} 项后续舰艇巡逻调用；"
                                        "舰艇巡逻仅在首次规划创建")
                    stage = "转换并校验任务参数"
                    try:
                        calls = validate_plan(plan, situation, self.registry)
                        break
                    except ValueError as exc:
                        repairable = ("同一批方案中单位重复分配",
                                      "飞机已被任务占用",
                                      "空中预警只能分配预警机",
                                      "空中预警每项只分配一架预警机",
                                      "空中预警 area", "空中预警进攻轴向",
                                      "area.attack_bearing_deg", "area.forward_km",
                                      "第二架预警机", "预警机间距不足", "预警任务最多",
                                      "预警机已在现有预警任务", "巡逻区间距不足",
                                      "预警区位置过前")
                        if attempt or not any(word in str(exc) for word in repairable):
                            raise
                        conflict_message = str(exc)
                        self.output(f"[第 {cycles} 轮] {conflict_message}；立即请 Agent 重规划一次")
                # Only a validated proposal consumes this planning snapshot.
                # Model/parameter failures retry on the next poll; execution
                # failures retain this checkpoint and their uncertainty locks.
                last_signature = signature
                last_planned_at = started
                last_planned_contacts = current_contacts
                record_plan(memory, plan, situation["scenario"]["start_time"])
                self._save_state()
                if navy_patrol_allowed:
                    self.state["navy_patrol_opening_done"] = True
                    self._save_state()
                if awacs_trigger["kind"] == "opening" and not any(
                        call.skill_id == "air.early_warning" for call in calls):
                    self.state["awacs_opening_attempt"] = opening_signature(situation)
                    self._save_state()
                for call in calls:
                    if call.skill_id == "air.early_warning" and call.area:
                        self.output(f"[第 {cycles} 轮] 预警区：{call.area['name']}；"
                                    f"{call.area['remark']}；参考点 {len(call.area['points'])}")
                self.output(f"[第 {cycles} 轮] Agent 方案：{plan.get('summary', '')}；"
                            f"Skill 调用 {len(calls)} 项")
                if not calls:
                    self.output(f"[第 {cycles} 轮] 无可创建任务；耗时 {self.clock()-started:.1f}s")
                    self._cleanup_areas(situation, cycles)
                    self.sleep(self.poll_interval_sec)
                    continue
                pending = []
                for call in calls:
                    record = self.state["missions"].get(call.mission_name)
                    updating = bool(record and call.existing_mission_guid and
                                    str(record.get("guid", "")).strip("{}").lower() ==
                                    call.existing_mission_guid.strip("{}").lower())
                    if record and not updating:
                        self.output(f"跳过已有任务：{call.mission_name}")
                        continue
                    if call.mission_name in self.state["uncertain"]:
                        self.output(f"跳过执行结果未确认的任务：{call.mission_name}；请核对墨子任务后处理 automatic_state.json 的 uncertain 记录")
                        continue
                    pending.append(call)
                if pending:
                    if "mission_assignments" in situation:
                        stage = "创建前核对飞机任务占用"
                        situation["mission_assignments"] = read_blue_mission_assignments(
                            situation["api_base"], situation["side"]["guid"],
                            {str(row.get("guid", "")) for row in situation["units"].get("air", [])})
                        validate_aircraft_availability(pending, situation)
                    stage = "编译并执行任务脚本"
                    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                    invocations = self.compiler.compile(pending, situation, run_id)
                    # 先完整校验本轮所有任务，再下发第一个创建请求。
                    self.executor.execute(invocations, dry_run=True)
                    workers = min(self.script_parallelism, len(invocations))
                    self.output(f"[第 {cycles} 轮] 执行 {len(invocations)} 项任务脚本；最大并行数 {workers}")
                    failures = []
                    cleaned_failure = False
                    execute_one = getattr(self.executor, "execute_prevalidated", None)
                    if execute_one is None:
                        execute_one = lambda item: self.executor.execute([item], dry_run=False)
                    # 每个 PowerShell 进程仅处理一个任务；同一任务内的墨子 API
                    # 创建区域、任务、单位分配和回读顺序保持不变。
                    with ThreadPoolExecutor(max_workers=workers,
                                            thread_name_prefix="mozi-mission") as pool:
                        futures = {
                            pool.submit(execute_one, item): item
                            for item in invocations
                        }
                        for future in as_completed(futures):
                            item = futures[future]
                            try:
                                results = future.result()
                                guid = _mission_guid(results)
                                if not guid:
                                    raise RuntimeError(f"{item.call.mission_name} 执行响应缺少任务 GUID")
                                record = self.state["missions"].get(item.call.mission_name)
                                self.state["missions"][item.call.mission_name] = {
                                    "guid": guid, "skill_id": item.call.skill_id,
                                    "report": str(item.report_path),
                                    "created_at_sim": (record.get("created_at_sim") if record else situation["scenario"]["start_time"]),
                                    "deployment": (item.call.area or {}).get("deployment", {}),
                                }
                                if item.call.skill_id == "air.early_warning":
                                    record_success(self.state, item.call, guid)
                                record_dispatch_outcome(
                                    memory, situation["scenario"]["start_time"],
                                    item.call.mission_name, item.call.skill_id,
                                    "updated" if item.call.existing_mission_guid else "created",
                                    guid)
                                self._save_state()
                                self.output(f"已{'更新' if item.call.existing_mission_guid else '创建'}：{item.call.mission_name} ({guid})")
                            except ScriptExecutionError as exc:
                                cleaned = any(result.phase == "execute"
                                              and "[NEW_MISSION_CLEANED]" in result.stderr
                                              for result in exc.results)
                                uncertain = (not cleaned and any(result.phase == "execute"
                                                                 for result in exc.results))
                                if uncertain:
                                    self._mark_uncertain(item, str(exc))
                                elif cleaned:
                                    self.state["uncertain"].pop(item.call.mission_name, None)
                                    cleaned_failure = True
                                    self.output(f"失败任务已删除并回读确认：{item.call.mission_name}；下轮重新规划")
                                record_dispatch_outcome(memory, situation["scenario"]["start_time"],
                                                        item.call.mission_name, item.call.skill_id,
                                                        "uncertain" if uncertain else "failed",
                                                        str(exc))
                                self._save_state()
                                failures.append(f"{item.call.mission_name}: {exc}")
                                self.output(f"创建失败：{item.call.mission_name}：{exc}")
                            except Exception as exc:
                                self._mark_uncertain(item, str(exc))
                                record_dispatch_outcome(memory, situation["scenario"]["start_time"],
                                                        item.call.mission_name, item.call.skill_id,
                                                        "uncertain", str(exc))
                                self._save_state()
                                failures.append(f"{item.call.mission_name}: {exc}")
                                self.output(f"创建结果未确认：{item.call.mission_name}：{exc}")
                    if cleaned_failure:
                        # Re-read contacts and rebuild the plan on the next poll.
                        last_signature = None
                        last_planned_at = -float("inf")
                        last_planned_contacts = None
                    # A failed peer task must not delay support for a strike
                    # that was created successfully in this same batch.
                    self._update_jammer(situation, cycles)
                    if failures:
                        self._cleanup_areas(situation, cycles)
                        raise RuntimeError(f"{len(failures)} 项任务执行失败；其余任务已分别记录："
                                           + "；".join(failures))
                self._cleanup_areas(situation, cycles)
                self.output(f"[第 {cycles} 轮] 完成，耗时 {self.clock()-started:.1f}s")
            except Exception as exc:
                self.output(f"[第 {cycles} 轮] {stage}失败：{type(exc).__name__}: {exc}")
                error_path = self.state_path.with_name("automatic_errors.log")
                try:
                    error_path.parent.mkdir(parents=True, exist_ok=True)
                    with error_path.open("a", encoding="utf-8") as log:
                        log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] "
                                  f"第 {cycles} 轮，阶段：{stage}\n")
                        log.write(traceback.format_exc())
                    self.output(f"详细堆栈：{error_path}")
                except OSError:
                    self.output("无法写入详细错误日志")
            if max_cycles is None or cycles < max_cycles:
                self.sleep(self.poll_interval_sec)
