"""Expose only owned CAP missions for area changes; preserve their aircraft set.

The live assignment map is authoritative. Saved mission records merely prove
which patrol missions belong to this project, so external tasks stay hidden.
"""

from __future__ import annotations

import json
from pathlib import Path


CAP_SKILL = "air.combat_patrol"


def _guid(value: object) -> str:
    return str(value or "").strip().strip("{}").lower()


def saved_current_area(record: dict) -> dict:
    """Recover the last configured patrol polygon for agent context."""
    try:
        report = Path(record["report"])
        config = report.parent.parent / "configs" / report.name
        area = json.loads(config.read_text(encoding="utf-8-sig"))["tasks"]["airCombatPatrol"]["area"]
        return {"name": area.get("name", ""), "points": area.get("points", [])}
    except (KeyError, TypeError, OSError, ValueError, AttributeError):
        return {}


def patrol_update_options(situation: dict) -> list[dict]:
    """Return active project CAP tasks and their complete live aircraft sets."""
    assignments = {_guid(unit): _guid(mission) for unit, mission in
                   (situation.get("mission_assignments") or {}).items()}
    aircraft = {_guid(row.get("guid")) for row in situation["units"].get("air", [])
                if isinstance(row, dict)}
    options = []
    for record in situation.get("existing_missions", []):
        if not isinstance(record, dict) or record.get("skill_id") != CAP_SKILL:
            continue
        mission_guid = _guid(record.get("guid"))
        name = str(record.get("name") or "").strip()
        if not mission_guid or not name:
            continue
        unit_guids = sorted(unit for unit, mission in assignments.items()
                            if mission == mission_guid and unit in aircraft)
        if unit_guids:
            options.append({"mission_name": name, "mission_guid": mission_guid,
                            "unit_guids": unit_guids,
                            "current_area": record.get("current_area") or {}})
    return options


def validate_air_call(call: dict, options: list[dict]) -> None:
    """An occupied CAP plane may only update its own mission with its whole group."""
    by_mission = {option["mission_guid"]: option for option in options}
    by_unit = {unit: option for option in options for unit in option["unit_guids"]}
    selected = {_guid(unit) for unit in call.get("unit_guids") or []}
    existing = _guid(call.get("existing_mission_guid"))
    if existing:
        option = by_mission.get(existing)
        if option is None:
            raise ValueError("空军 Agent 只能更新本项目已登记且仍有飞机的战斗巡逻任务")
        if (call.get("skill_id") != CAP_SKILL
                or call.get("mission_name") != option["mission_name"]
                or selected != set(option["unit_guids"])):
            raise ValueError("更新战斗巡逻区必须保留原任务名称、Skill 和全部已分配飞机")
    elif selected.intersection(by_unit):
        raise ValueError("巡逻中的飞机只能更新原巡逻任务，不能创建新任务")
