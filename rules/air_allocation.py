"""Explain every fighter in the air Agent input without relaxing assignment safety.

MissionGant records task allocation, not proof of aircraft sortie.
"""

from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer


def _guid(value):
    return str(value or "").strip("{}").lower()


def fighter_allocation(situation):
    """Return compact available and assigned lists for all known fighters."""
    assignment = {_guid(unit): _guid(mission) for unit, mission in
                  (situation.get("mission_assignments") or {}).items()}
    missions = {_guid(row.get("guid")): row for row in
                (situation.get("existing_missions") or []) if isinstance(row, dict)}
    result = {"available": [], "assigned": []}
    for unit in situation.get("units", {}).get("air", []):
        if not isinstance(unit, dict) or is_awacs(unit) or is_jammer(unit):
            continue
        guid = _guid(unit.get("guid"))
        if not guid:
            continue
        row = {"guid": guid, "name": str(unit.get("name") or ""),
               "airborne": unit.get("airborne"),
               "in_facility": unit.get("in_facility")}
        for key in ("loadout_name", "strike_capabilities", "weapon_info_status"):
            if key in unit:
                row[key] = unit[key]
        mission_guid = assignment.get(guid)
        if mission_guid:
            mission = missions.get(mission_guid) or {}
            row.update({"mission_guid": mission_guid,
                        "mission_name": str(mission.get("name") or ""),
                        "mission_skill_id": str(mission.get("skill_id") or "")})
            result["assigned"].append(row)
        else:
            result["available"].append(row)
    return result
