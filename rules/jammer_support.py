"""Deterministic trigger and geometry for aircraft jamming support.

The jammer is controlled independently of LLM Skill calls and the old BT
project. A strike mission is a prerequisite; it is not evidence of takeoff.
"""

from __future__ import annotations

import math


STRIKE_SKILLS = frozenset({"air.air_strike", "air.sea_strike", "air.land_strike"})
_JAMMER_MARKERS = ("EC-130H", "EA-18G", "电子干扰", "电子战", "JAMMER", "OECM")
_EARTH_RADIUS_KM = 6371.0088


def is_jammer(unit: dict) -> bool:
    """Classify only the observed aircraft, never infer one from a contact."""
    label = f"{unit.get('name', '')} {unit.get('sub_type', '')}".upper()
    return any(marker in label for marker in _JAMMER_MARKERS)


def is_strike_record(record: dict) -> bool:
    return record.get("skill_id") in STRIKE_SKILLS and bool(record.get("guid"))


def _position(unit: dict) -> tuple[float, float] | None:
    try:
        lat, lon = unit["lat"], unit["lon"]
        if type(lat) is bool or type(lon) is bool:
            return None
        lat, lon = float(lat), float(lon)
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 <= lat <= 85 and -180 <= lon <= 180):
        return None
    return lat, lon


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    hav = (math.sin((lat2 - lat1) / 2) ** 2
           + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(hav)))


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlon = lon2 - lon1
    east = math.sin(dlon) * math.cos(lat2)
    north = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(east, north)) + 360) % 360


def _destination(origin: tuple[float, float], bearing_deg: float, km: float) -> tuple[float, float]:
    lat1, lon1 = map(math.radians, origin)
    bearing, arc = math.radians(bearing_deg), km / _EARTH_RADIUS_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(arc)
                     + math.cos(lat1) * math.sin(arc) * math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing) * math.sin(arc) * math.cos(lat1),
                             math.cos(arc) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lon2) + 180) % 360 - 180


def striker_aircraft(detail: dict, aircraft: list[dict]) -> list[dict]:
    """Return real non-jammer aircraft assigned to a target-bearing strike."""
    if not (detail.get("strikeMissionData") or {}).get("targetList"):
        return []
    by_guid = {str(unit.get("guid", "")).strip("{}").lower(): unit for unit in aircraft}
    assigned = [str(item.get("key", "")).strip("{}").lower()
                for group in (detail.get("missionBasicData") or {}).get("assignedCombatUnit") or []
                for item in group.get("children") or []]
    candidates = [by_guid[guid] for guid in assigned
                  if guid in by_guid and not is_jammer(by_guid[guid])]
    return sorted(candidates, key=lambda unit: str(unit.get("guid", "")))


def package_center(aircraft: list[dict]) -> dict | None:
    """Track the airborne package when possible, otherwise its launch group."""
    airborne = [unit for unit in aircraft if unit.get("airborne") is True
                and unit.get("in_facility") is not True and _position(unit)]
    points = [_position(unit) for unit in (airborne or aircraft)]
    points = [point for point in points if point]
    if not points:
        return None
    return {"lat": sum(point[0] for point in points) / len(points),
            "lon": sum(point[1] for point in points) / len(points),
            "airborne": bool(airborne)}


def follow_waypoint(striker: dict, jammer: dict,
                    previous_striker: tuple[float, float] | None,
                    trail_km: float) -> dict[str, float] | None:
    """Put the jammer behind observed striker motion only."""
    lead, escort = _position(striker), _position(jammer)
    if lead is None or escort is None or not (0 <= trail_km <= 100):
        return None
    if previous_striker and distance_km(previous_striker, lead) >= 1:
        backward = (_bearing_deg(previous_striker, lead) + 180) % 360
    else:
        return None  # Never guess 'behind' from the jammer's own position.
    lat, lon = _destination(lead, backward, trail_km)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def should_follow(record: dict, striker: dict, now: float, *, interval_sec: float) -> bool:
    """Bound REST traffic while refreshing moving waypoints and emitters."""
    if record.get("phase") != "following":
        return now - float(record.get("last_follow_at", 0)) >= min(interval_sec, 10)
    previous = record.get("last_striker_position")
    current = _position(striker)
    if current and isinstance(previous, list) and len(previous) == 2:
        if distance_km(tuple(previous), current) >= 5:
            return True
    return now - float(record.get("last_follow_at", 0)) >= interval_sec
