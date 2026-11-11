"""Find current air-strike contacts within the user-set 1000 km task radius.

The radius is a mission-creation rule, measured from each free aircraft's
current position to a current blue-side contact. It is not a weapon range,
loadout-specific combat radius, or promise of reaching/engaging the target.
No historical contact or aircraft already assigned to a Mozi task qualifies.
"""

from __future__ import annotations

import math
from uuid import UUID

from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer
from rules.air_weapon_roles import compatible_with_skill


RADIUS_KM = 1000.0
EARTH_RADIUS_KM = 6371.0088
SKILL_CONTACT_GROUP = {
    "air.air_strike": "air",
    "air.sea_strike": "sea",
    "air.land_strike": "land",
}
REVIEW_PHASES = frozenset(("打击优先", "抓紧打击", "临近结束"))


def _guid(value: object) -> str:
    try:
        return str(UUID(str(value).strip().strip("{}")))
    except (ValueError, TypeError, AttributeError):
        return ""


def _point(row: dict) -> tuple[float, float] | None:
    lat, lon = row.get("lat"), row.get("lon")
    if (type(lat) not in (int, float) or type(lon) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
        return None
    return float(lat), float(lon)


def _distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat = lat2 - lat1
    dlon = (lon2 - lon1 + math.pi) % (2 * math.pi) - math.pi
    value = (math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2)
             * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(value)))


def strike_opportunities(situation: dict, air_skills: list[dict],
                         air_units: list[dict]) -> dict:
    """Return contact GUIDs eligible for aircraft strike creation by Skill.

    An available aircraft must be a non-AWACS/non-jammer Aircraft with a valid
    current position and no entry in the live mission-assignment map. The rule
    deliberately does not query or estimate loadout combat radius.
    """
    result = {"radius_km": RADIUS_KM,
              "eligible_by_skill": {skill: [] for skill in SKILL_CONTACT_GROUP}}
    occupied = {_guid(guid) for guid in (situation.get("mission_assignments") or {})}
    free_positions = {skill: [] for skill in SKILL_CONTACT_GROUP}
    live_inventory = bool(situation.get("weapon_inventory_live"))
    for unit in air_units:
        if not isinstance(unit, dict):
            continue
        guid = _guid(unit.get("guid"))
        if (not guid or guid in occupied
                or unit.get("unit_type") not in ("Aircraft", "飞机")
                or is_awacs(unit) or is_jammer(unit)):
            continue
        point = _point(unit)
        if point is not None:
            for skill in SKILL_CONTACT_GROUP:
                if compatible_with_skill(unit, skill, live=live_inventory):
                    free_positions[skill].append(point)
    if not any(free_positions.values()):
        return result

    registered = {str(skill.get("skill_id")) for skill in air_skills
                  if isinstance(skill, dict)}
    contacts = situation.get("contacts") or {}
    if not isinstance(contacts, dict):
        return result
    for skill, group in SKILL_CONTACT_GROUP.items():
        if skill not in registered or not free_positions[skill]:
            continue
        seen = set()
        for contact in contacts.get(group, []):
            if not isinstance(contact, dict):
                continue
            guid = _guid(contact.get("guid"))
            if not guid or guid in seen:
                continue
            point = _point(contact)
            if point is None:
                continue
            seen.add(guid)
            nearest = min(_distance_km(unit, point) for unit in free_positions[skill])
            if nearest <= RADIUS_KM:
                result["eligible_by_skill"][skill].append({
                    "contact_guid": guid,
                    "nearest_distance_km": round(nearest, 1),
                })
        result["eligible_by_skill"][skill].sort(
            key=lambda row: (row["nearest_distance_km"], row["contact_guid"]))
    return result


def review_focus(opportunities: dict, proposal: dict,
                 planning_priority: str) -> str | None:
    """Name one missing strike Skill for an air-Agent review in strike phases.

    Air contacts have priority over sea and land contacts. This only requests
    another assessment; it never constructs or dispatches a strike mission.
    """
    if planning_priority not in REVIEW_PHASES:
        return None
    eligible = opportunities.get("eligible_by_skill") or {}
    first = next((skill for skill in SKILL_CONTACT_GROUP if eligible.get(skill)), None)
    if first is None:
        return None
    calls = proposal.get("skill_calls") or []
    if any(isinstance(call, dict) and call.get("skill_id") == first for call in calls):
        return None
    return first
