"""按专业 Agent 筛选输入；执行校验仍使用完整态势。

已登记的专用规则只发给对应 Agent。未知规则暂按共用规则保留，新增
专用规则时应登记 RULE_OWNERS，避免把其它军种的参数塞进上下文。
未知归属任务保留，防止隐藏旧版本或外部创建的任务。
"""

from copy import deepcopy

# Keep this rule importable without runtime/__init__.py, which also exports the
# planner and would otherwise import this module while it is being initialized.
CONTEXT_OWNERS = frozenset(("air", "awacs", "navy", "army"))
RULE_OWNERS = {
    "navy_patrol_opening_allowed": {"navy"},
    "awacs_trigger": {"awacs"},
    "awacs_min_separation_km": {"awacs"},
    "patrol_spacing": {"air", "awacs"},
    "suspected_air_attack_axis": {"air", "awacs"},
    "air_strike_opportunities": {"air"},
    "sea_strike_allocation": {"air"},
    "campaign_phase": {"air", "awacs"},
}


def service_rules(rules: dict, owner: str) -> dict:
    """联合指挥仍看完整规则；这里只裁剪专业 Agent 的可见输入。"""
    if owner not in CONTEXT_OWNERS:
        raise ValueError(f"未知 Agent：{owner}")
    return deepcopy({key: value for key, value in rules.items()
                     if key not in RULE_OWNERS or owner in RULE_OWNERS[key]})


def service_missions(missions: list[dict], owner: str, registry) -> list[dict]:
    """按 Skill 的 agent_owner 筛选已知任务，保留归属未知的任务。"""
    if owner not in CONTEXT_OWNERS:
        raise ValueError(f"未知 Agent：{owner}")
    result = []
    for mission in missions:
        try:
            mission_owner = registry.get(str(mission.get("skill_id", ""))).agent_owner
        except ValueError:
            mission_owner = None
        if mission_owner is None or mission_owner == owner:
            result.append(deepcopy(mission))
    return result
