"""Clear a stale AWACS execution lock only after the live task is absent.

An interrupted request may have succeeded despite a failed response. The
current blue mission list must therefore be read successfully first; if a
matching task still exists, the lock remains and no duplicate is created.
"""

from __future__ import annotations

import json
from pathlib import Path


AWACS_SKILL = "air.early_warning"


def _guid(value: object) -> str:
    return str(value or "").strip().strip("{}").lower()


def _awacs_units(record: dict) -> list[str] | None:
    """Return known aircraft IDs, or None if an old record cannot be proved AWACS."""
    if record.get("skill_id") == AWACS_SKILL:
        values = record.get("unit_guids")
        if isinstance(values, list):
            return [_guid(value) for value in values]
    try:
        config = json.loads(Path(record["config"]).read_text(encoding="utf-8-sig"))
        task = config["tasks"]["airEarlyWarning"]
        if task.get("enabled") is True and isinstance(task.get("unitGuids"), list):
            return [_guid(value) for value in task["unitGuids"]]
    except (KeyError, TypeError, OSError, ValueError, AttributeError):
        pass
    return None


def resolve_uncertain_awacs(state: dict, situation: dict,
                            live_missions: list[dict]) -> str:
    """Mutate state only when all known uncertain AWACS tasks are absent."""
    candidates = {}
    for name, record in (state.get("uncertain") or {}).items():
        if isinstance(record, dict) and (units := _awacs_units(record)) is not None:
            candidates[name] = units
    if not candidates:
        return "旧预警执行记录无法识别，保持阻断以防重复创建"
    live_names = {row["name"] for row in live_missions}
    if any(name in live_names for name in candidates):
        return "墨子已有同名预警任务，保持阻断并等待核对"
    assignments = {_guid(unit) for unit in (situation.get("mission_assignments") or {})}
    if any(unit in assignments for units in candidates.values() for unit in units):
        return "原预警机已分配其他任务，保持阻断以防重复分配"
    for name in candidates:
        state["uncertain"].pop(name, None)
    state["uncertain_awacs"] = False
    state.pop("awacs_opening_attempt", None)
    state.pop("awacs_uncertain_reason", None)
    return "cleared"
