"""Scenario-scoped reconnaissance -> air control -> aviation sea-strike gate.

The joint LLM assesses air control; code enforces sequencing and an explicit
current assessment. Time, contact proximity, or mission creation alone never
authorizes sea strikes. A grounded force cannot substantiate completed air
control in this scenario. This module does not judge victory or combat effects.
"""

from copy import deepcopy

from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer

PHASES = ("reconnaissance", "air_control", "sea_strike")
LABELS = {"reconnaissance": "侦察部署", "air_control": "争夺制空", "sea_strike": "对海打击"}
SEA_SKILL = "air.sea_strike"


def _guid(value):
    return str(value or "").strip().strip("{}").lower()


def observation(situation):
    assignments = {_guid(k): _guid(v) for k, v in (situation.get("mission_assignments") or {}).items()}
    fighters, awacs = [], []
    for unit in situation.get("units", {}).get("air", []):
        if not isinstance(unit, dict) or unit.get("airborne") is not True:
            continue
        guid = _guid(unit.get("guid"))
        if not guid or not assignments.get(guid):
            continue
        if is_awacs(unit):
            awacs.append(guid)
        elif not is_jammer(unit):
            fighters.append(guid)
    return {"airborne_assigned_fighter_guids": sorted(set(fighters)),
            "airborne_assigned_awacs_guids": sorted(set(awacs)),
            "current_enemy_air_contact_guids": sorted({_guid(c.get("guid"))
                for c in situation.get("contacts", {}).get("air", []) if c.get("guid")})}


def phase_context(state, situation, enabled=True):
    state = state if isinstance(state, dict) else {}
    phase = state.get("phase", "reconnaissance")
    if phase not in PHASES:
        phase = "reconnaissance"
    return {"enabled": bool(enabled), "phase": phase,
            # Past authorization is context, never a fresh permission token.
            "sea_strike_allowed": False if enabled else True,
            "previous_assessment": deepcopy(state.get("assessment", {})),
            "observation": observation(situation),
            "policy": "先侦察、再争夺制空；联合指挥依据实际态势确认制空后，规则才开放航空对海任务"}


def evaluate_phase(context, directive, situation):
    context = context if isinstance(context, dict) else {}
    enabled = context.get("enabled") is True
    previous = context.get("phase", "reconnaissance")
    if previous not in PHASES:
        previous = "reconnaissance"
    result = {"enabled": enabled, "phase": previous,
              "sea_strike_allowed": not enabled, "reason": "未启用阶段限制",
              "evidence": [], "assessment": {},
              "observation": observation(situation),
              "evaluated_at": str(situation.get("scenario", {}).get("start_time") or "")}
    if not enabled:
        return result
    directive = directive if isinstance(directive, dict) else {}
    requested = directive.get("operation_phase")
    assessment = directive.get("air_control_assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    status = assessment.get("status")
    evidence = assessment.get("evidence")
    evidence = [x.strip()[:400] for x in evidence[:5] if isinstance(x, str) and x.strip()] if isinstance(evidence, list) else []
    result.update(assessment={"status": status, "evidence": evidence}, evidence=evidence)
    if requested not in PHASES:
        result.update(phase="air_control" if previous == "sea_strike" else previous,
                      reason="联合指挥未明确阶段，暂不开放对海任务")
    elif requested == "reconnaissance":
        result.update(phase=requested, reason="联合指挥维持侦察部署，对海任务暂未开放")
    elif requested == "air_control":
        result.update(phase=requested, reason="正在争夺制空；允许巡逻推进和对空打击，对海任务暂未开放")
    elif previous == "reconnaissance":
        result.update(phase="air_control", reason="不能从侦察直接跳到对海打击，先执行并评估制空行动")
    elif status != "established" or not evidence:
        result.update(phase="air_control", reason="缺少联合指挥有依据的制空完成结论，对海任务暂未开放")
    elif not result["observation"]["airborne_assigned_fighter_guids"]:
        result.update(phase="air_control", reason="尚无实际在空且执行任务的战斗机，任务创建或地面待命不等于取得制空")
    else:
        result.update(phase="sea_strike", sea_strike_allowed=True,
                      reason="联合指挥已依据当前态势确认制空，开放航空对海任务")
    return result


def suppress_sea_calls(calls, phase):
    if not phase.get("enabled") or phase.get("sea_strike_allowed") is True:
        return deepcopy(calls), []
    return ([deepcopy(c) for c in calls if c.get("skill_id") != SEA_SKILL],
            [deepcopy(c) for c in calls if c.get("skill_id") == SEA_SKILL])


def validate_phase_calls(calls, plan, situation):
    context = (situation.get("operational_rules") or {}).get("campaign_phase") or {}
    phase = evaluate_phase(context, plan.get("joint_directive"), situation)
    # Recompute from the input state and directive, ignoring a caller-supplied
    # campaign_phase.sea_strike_allowed flag in the output plan.
    if phase["enabled"] and not phase["sea_strike_allowed"]:
        if any(c.skill_id == SEA_SKILL for c in calls):
            raise ValueError("航空对海任务未获阶段许可：" + phase["reason"])
