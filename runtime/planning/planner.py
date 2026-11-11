"""联合指挥 + 空军/预警机/海军/陆军 Agent：只输出任务 Skill 调用。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time
from typing import Any

from agents.model_client import AgentConfig, ChatCompletionClient, ModelError
from runtime.planning.plan_adapter import normalize_plan

from runtime.contracts import AGENT_OWNERS, SERVICES
from runtime.skills.registry import SkillRegistry
from agents import (AirCommanderAgent, ArmyCommanderAgent, AwacsCommanderAgent,
                    JointCommanderAgent, NavyCommanderAgent)
from agents.prompt_loader import load_prompt
from rules.awacs_area import is_awacs
from rules.awacs_deployment import minimum_separation_km
from rules.patrol_spacing import patrol_spacing_advisory
from rules.navy_patrol import available_skills
from rules.agent_activation import skip_reason
from rules.air_patrol_update import patrol_update_options, validate_air_call
from rules.joint_refresh import refresh_reason, strategic_snapshot
from rules.scenario_timeline import scenario_timeline
from rules.situation_memory import filter_brief
from rules.threat_axis import suspected_air_attack_axis
from rules.jammer_support import is_jammer
from rules.air_strike_review import strike_opportunities, review_focus
from rules.air_allocation import fighter_allocation
from rules.sea_strike_allocation import allocation, apply_allocation
from rules.campaign_phase import evaluate_phase, observation, suppress_sea_calls
from rules.agent_context import service_rules, service_missions
from rules.plan_review import merge_review_calls


class HierarchicalSkillPlanner:
    def __init__(self, config: AgentConfig, registry: SkillRegistry,
                 client: ChatCompletionClient | None = None, clock=time.monotonic):
        self.config = config
        self.registry = registry
        self.client = client or ChatCompletionClient(config)
        self.clock = clock
        self._joint_cache: dict[str, Any] | None = None
        self.joint_agent = JointCommanderAgent(self.client, load_prompt("joint"))
        self.service_agents = {
            "air": AirCommanderAgent(self.client, load_prompt("air")),
            "awacs": AwacsCommanderAgent(self.client, load_prompt("awacs")),
            "navy": NavyCommanderAgent(self.client, load_prompt("navy")),
            "army": ArmyCommanderAgent(self.client, load_prompt("army")),
        }

    @classmethod
    def from_config(cls, path, registry: SkillRegistry):
        return cls(AgentConfig.load(path), registry)

    def set_monitor_context(self, **values):
        setter = getattr(self.client, "set_trace_scope", None)
        if callable(setter):
            setter(**values)

    def reset_cached_directive(self):
        """想定时间倒退时清除上一局的联合方案缓存。"""
        self._joint_cache = None

    @staticmethod
    def _joint(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("联合指挥 Agent 输出必须为 version=1 的对象")
        intents = raw.get("mission_intents")
        if not isinstance(intents, list) or len(intents) > 30:
            raise ValueError("联合指挥 mission_intents 必须是最多30项的数组")
        for item in intents:
            if not isinstance(item, dict) or item.get("service") not in AGENT_OWNERS:
                raise ValueError("联合指挥任务意图必须归属 air/awacs/navy/army")
            if any(key in item for key in ("unit_id", "unit_guids", "area", "skill_calls")):
                raise ValueError("联合指挥不得选择具体单位、区域或 Skill 调用")
        return raw

    @staticmethod
    def _component(raw: dict[str, Any], service: str) -> dict[str, Any]:
        if (not isinstance(raw, dict) or raw.get("version") != 1
                or raw.get("service") != service):
            raise ValueError(f"{service} Agent 输出协议无效")
        calls = raw.get("skill_calls")
        if not isinstance(calls, list) or len(calls) > 100:
            raise ValueError(f"{service} Agent skill_calls 必须是最多100项的数组")
        for index, call in enumerate(calls, 1):
            if not isinstance(call, dict):
                raise ValueError(f"{service} Agent skill_calls 第 {index} 项必须是 JSON 对象")
        return raw

    def plan(self, situation: dict[str, Any], instruction: str) -> dict[str, Any]:
        rules = dict(situation.get("operational_rules") or {})
        # Use the same deterministic simulation-time reminder for joint and
        # service Agents, including the offline ``run.py plan`` entry point.
        rules["scenario_time"] = scenario_timeline(situation.get("scenario"))
        rules.pop("suspected_air_attack_axis", None)
        suspected_axis = suspected_air_attack_axis(situation)
        if suspected_axis:
            rules["suspected_air_attack_axis"] = suspected_axis
        rules["awacs_min_separation_km"] = minimum_separation_km(situation)
        rules["patrol_spacing"] = patrol_spacing_advisory(
            situation.get("existing_areas", []), situation=situation)
        navy_patrol_allowed = rules.get("navy_patrol_opening_allowed", True)

        catalogs = {
            owner: available_skills(owner, self.registry.public_catalog_for_agent(owner),
                                    navy_patrol_allowed)
            for owner in AGENT_OWNERS
        }

        aircraft = situation["units"].get("air", [])
        occupied_aircraft = {
            str(guid).strip("{}").lower()
            for guid in (situation.get("mission_assignments") or {})
        }
        patrol_updates = patrol_update_options(situation)
        patrol_aircraft = {guid for option in patrol_updates
                           for guid in option["unit_guids"]}
        own_unit_rows = {
            "air": [row for row in aircraft
                    if not is_awacs(row) and not is_jammer(row)
                    and (str(row.get("guid", "")).strip("{}").lower() not in occupied_aircraft
                         or str(row.get("guid", "")).strip("{}").lower() in patrol_aircraft)],
            "awacs": [row for row in aircraft if is_awacs(row)],
            "navy": situation["units"].get("navy", []),
            "army": situation["units"].get("army", []),
        }
        # This is a user-defined mission-creation threshold, not a claim
        # about actual weapon or loadout performance. The rule uses only current
        # contacts and aircraft positions; no loadout-radius lookup is made.
        rules["air_strike_opportunities"] = strike_opportunities(
            situation, catalogs["air"], own_unit_rows["air"])
        rules["sea_strike_allocation"] = allocation(situation)
        # Do not spend a model review on a sea strike that cannot form a pair.
        grouped_targets = {g["target_contact_guid"]
                           for g in rules["sea_strike_allocation"]["groups"]}
        sea_options = rules["air_strike_opportunities"]["eligible_by_skill"]["air.sea_strike"]
        rules["air_strike_opportunities"]["eligible_by_skill"]["air.sea_strike"] = [
            row for row in sea_options if row["contact_guid"] in grouped_targets]
        skips = {
            owner: skip_reason(owner, own_unit_rows[owner], catalogs[owner],
                               situation["contacts"], rules)
            for owner in AGENT_OWNERS
        }

        # Validation retries append repair advice for service Agents. They do not
        # change the top-level commander's objective or invalidate its cache.
        joint_instruction = instruction.split("\n上一版任务方案校验失败：", 1)[0]
        snapshot = strategic_snapshot(situation)
        phase_input = deepcopy(rules.get("campaign_phase") or {})
        if phase_input.get("enabled"):
            snapshot["campaign_phase"] = {"phase": phase_input.get("phase"),
                                          "observation": observation(situation)}
        now = self.clock()
        refresh = refresh_reason(self._joint_cache, snapshot, joint_instruction,
                                 now, self.config.joint_interval_sec)
        if refresh:
            joint_context = {
                "objective": self.config.objective,
                "instruction": joint_instruction,
                "situation": deepcopy({key: value for key, value in situation.items()
                                       if key != "_service_situation_memory"}),
                "available_task_skills": {
                    owner: ([] if skips[owner] else catalogs[owner])
                    for owner in AGENT_OWNERS
                },
            }
            joint_context["situation"]["operational_rules"] = deepcopy(rules)
            # Strategic command only needs the compact conflict summary;
            # polygon vertices are reserved for the aircraft planners.
            joint_context["situation"].pop("existing_areas", None)
            directive = self._joint(self.joint_agent.decide(joint_context))
            self._joint_cache = {"instruction": joint_instruction, "snapshot": snapshot,
                                 "at": now, "directive": deepcopy(directive)}
        else:
            directive = deepcopy(self._joint_cache["directive"])
            trace = getattr(self.client, "trace_event", None)
            if callable(trace):
                trace("joint", "cache", reason="联合方案尚在复用期且无关键态势变化",
                      output=deepcopy(directive))

        phase_result = evaluate_phase(phase_input, directive, situation)
        if phase_result["enabled"]:
            rules["campaign_phase"] = deepcopy(phase_result)
            if not phase_result["sea_strike_allowed"]:
                catalogs["air"] = [s for s in catalogs["air"] if s["skill_id"] != "air.sea_strike"]
                rules["air_strike_opportunities"]["eligible_by_skill"]["air.sea_strike"] = []
                rules["sea_strike_allocation"].update(groups=[], phase_blocked=True,
                                                     reason=phase_result["reason"])
                skips["air"] = skip_reason("air", own_unit_rows["air"], catalogs["air"],
                                           situation["contacts"], rules)

        def decide(owner: str) -> dict[str, Any]:
            if skips[owner]:
                proposal = {"version": 1, "service": owner,
                            "summary": f"待命：{skips[owner]}", "skill_calls": []}
                trace = getattr(self.client, "trace_event", None)
                if callable(trace):
                    trace(f"component_{owner}", "skip", reason=skips[owner],
                          input={"own_units": deepcopy(own_unit_rows[owner]),
                                 "task_skills": deepcopy(catalogs[owner])},
                          output=deepcopy(proposal))
                return proposal
            context = {
                "objective": self.config.objective,
                "instruction": instruction,
                "joint_directive": directive,
                "operational_rules": service_rules(rules, owner),
                "own_units": deepcopy(own_unit_rows[owner]),
                "friendly_context": {
                    key: deepcopy(value) for key, value in situation["units"].items()
                    if key in SERVICES and key != ("air" if owner == "awacs" else owner)
                },
                "contacts": deepcopy(situation["contacts"]),
                "existing_missions": service_missions(
                    situation.get("existing_missions", []), owner, self.registry),
                "existing_areas": deepcopy(situation.get("existing_areas", []))
                if owner in ("air", "awacs") else [],
                "task_skills": catalogs[owner],
            }
            if owner == "air":
                context["fighter_allocation"] = fighter_allocation(situation)
                context["patrol_updates"] = deepcopy(patrol_updates)
            if situation.get("situation_memory"):
                context["situation_memory"] = deepcopy(
                    (situation.get("_service_situation_memory") or {}).get(owner)
                    or filter_brief(situation["situation_memory"], owner))
            proposal = self._component(self.service_agents[owner].decide(context), owner)
            proposal["skill_calls"] = normalize_plan({
                "version": 1, "skill_calls": proposal["skill_calls"]
            })["skill_calls"]
            if owner == "air":
                focus = review_focus(
                    rules["air_strike_opportunities"], proposal,
                    rules["scenario_time"]["planning_priority"])
                if focus:
                    # Reconsider only the air proposal. Cached joint intent is
                    # not a weapon-range fact and other Agents need no re-call.
                    retry_context = deepcopy(context)
                    retry_context["planning_feedback"] = {
                        "rule": "air_strike_1000km_review",
                        "required_skill_id": focus,
                        "eligible_current_contacts": deepcopy(
                            rules["air_strike_opportunities"]["eligible_by_skill"][focus]),
                        "previous_proposal": deepcopy(proposal),
                        "instruction": (
                            "当前目标在用户规定的飞机打击任务1000公里创建范围内。"
                            "联合方案中的距离判断不是武器射程校验。请优先提交一项"
                            f"{focus} 调用；只有本轮输入中明确的燃油、损伤、"
                            "武器不适配或单位占用等事实才可暂缓，并在summary写清。"
                            "缺loadout_guid、未知精确射程、目标远离或单纯距离远"
                            "不能单独阻断。保留不冲突的原巡逻任务更新。"
                        ),
                    }
                    review = {
                        "triggered": True, "focus_skill_id": focus,
                        "strike_submitted": False,
                    }
                    try:
                        revised = self._component(
                            self.service_agents[owner].decide(retry_context), owner)
                        revised["skill_calls"] = normalize_plan({
                            "version": 1, "skill_calls": revised["skill_calls"]
                        })["skill_calls"]
                        submitted = any(call.get("skill_id") == focus
                                        for call in revised["skill_calls"])
                        review["strike_submitted"] = submitted
                        if submitted:
                            # Both are unexecuted proposals. Keep independent
                            # calls omitted by the review; revised choices win
                            # when identities or aircraft allocation conflict.
                            merged, merge_report = merge_review_calls(
                                proposal["skill_calls"], revised["skill_calls"])
                            revised["skill_calls"] = merged
                            for key, value in merge_report.items():
                                if value:
                                    review[key] = value
                            preserved = merge_report["preserved_call_ids"]
                            if preserved:
                                revised["summary"] = (
                                    f"复评补充方案，并保留原方案中 {len(preserved)} 项独立调用；"
                                    + str(revised.get("summary") or ""))
                            proposal = revised
                        else:
                            # No strike was added: preserve both the first
                            # answer's calls and the summary describing them.
                            review["revised_summary"] = str(revised.get("summary") or "")
                    except (ModelError, ValueError) as exc:
                        # An optional review must not discard already returned
                        # proposals from this or other service Agents. Normal
                        # plan validation still runs after this fallback.
                        review["error"] = {
                            "type": type(exc).__name__, "message": str(exc),
                        }
                    proposal["air_strike_review"] = review
                    trace = getattr(self.client, "trace_event", None)
                    if callable(trace):
                        trace("component_air", "rule_review",
                              reason=("可选复评失败，保留首次方案" if "error" in review
                                      else "1000公里内有当前目标和空闲飞机，原方案未提交对应打击"),
                              output=deepcopy(review))
            return proposal

        with ThreadPoolExecutor(max_workers=self.config.component_parallelism,
                                thread_name_prefix="skill-planner") as pool:
            proposals = dict(zip(AGENT_OWNERS, pool.map(decide, AGENT_OWNERS)))
        for owner, proposal in proposals.items():
            # Even if a model ignores its filtered catalog, do not execute a
            # sea strike before the joint assessment releases this phase.
            if owner == "air":
                kept, suppressed = suppress_sea_calls(proposal["skill_calls"], phase_result)
                if suppressed:
                    proposal["skill_calls"] = kept
                    proposal["phase_suppressed_calls"] = suppressed
                    proposal["summary"] = (str(proposal.get("summary") or "") +
                        f"；阶段规则暂缓{len(suppressed)}项航空对海任务：" + phase_result["reason"])
                    trace = getattr(self.client, "trace_event", None)
                    if callable(trace):
                        trace("component_air", "phase_gate", reason=phase_result["reason"],
                              output={"suppressed_calls": deepcopy(suppressed),
                                      "phase": deepcopy(phase_result)})
            own_ids = {
                str(item.get("guid", item.get("id", ""))).strip().strip("{}").lower()
                for item in own_unit_rows[owner] if isinstance(item, dict)
            }
            for call in proposal["skill_calls"]:
                spec = self.registry.get(str(call.get("skill_id", "")))
                if spec.agent_owner != owner:
                    raise ValueError(f"{owner} Agent 越权调用了 {spec.agent_owner} Skill：{spec.skill_id}")
                selected = call.get("unit_guids") or []
                if any(str(guid).strip().strip("{}").lower() not in own_ids for guid in selected):
                    raise ValueError(f"{owner} Agent 使用了不属于 own_units 的主任务单元")
                if owner == "air":
                    validate_air_call(call, patrol_updates)
                    escorts = call.get("escort_unit_guids") or []
                    if any(str(guid).strip().strip("{}").lower() not in own_ids
                           for guid in escorts):
                        raise ValueError("air Agent 使用了不属于 own_units 的护航单元")
        # Apply after optional model review and ownership checks, so a later
        # review cannot undo the deterministic minimum-two distribution.
        air = proposals["air"]
        allocated, allocation_report = apply_allocation(air["skill_calls"], situation)
        if allocation_report is not None:
            air["model_summary"] = air.get("summary", "")
            air["skill_calls"] = allocated
            air["sea_strike_allocation"] = allocation_report
            distribution = "、".join(
                f"{g['target_name']} {len(g['unit_guids'])}架"
                for g in allocation_report["groups"])
            air["summary"] = (str(air.get("summary") or "") +
                "；对海规则实际分配：" + (distribution or "无满足双机条件的新任务，待命") +
                f"；未分配对海飞机{len(allocation_report['waiting_unit_guids'])}架")
            if allocation_report.get("rejected_calls"):
                air["summary"] += (f"；暂缓{len(allocation_report['rejected_calls'])}项无效对海调用："
                                   "目标缺失或已不在当前水面接触，其他合法任务继续")
            trace = getattr(self.client, "trace_event", None)
            if callable(trace):
                trace("component_air", "rule_allocation", reason="对海目标至少双机，整数均分",
                      output=deepcopy(allocation_report))
        skill_calls = [call for owner in AGENT_OWNERS for call in proposals[owner]["skill_calls"]]
        return {
            "version": 1,
            "summary": "；".join(
                str(proposals[owner].get("summary", "")).strip()
                for owner in AGENT_OWNERS if str(proposals[owner].get("summary", "")).strip()
            ),
            "joint_directive": directive,
            "campaign_phase": phase_result,
            "joint_source": "model" if refresh else "cache",
            "joint_refresh_reason": refresh,
            "agent_skips": {owner: reason for owner, reason in skips.items() if reason},
            "service_proposals": proposals,
            "skill_calls": skill_calls,
        }
