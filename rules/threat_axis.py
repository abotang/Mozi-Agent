"""Infer a suspected air approach direction from one *current* AEW contact.

This is a planning hypothesis, not a new contact or proof of accompanying aircraft.
Only current contacts may enter this rule; historical tracks are never targets.
"""

from __future__ import annotations

import math


_EARTH_RADIUS_KM = 6371.0088
_EARLY_WARNING_MARKERS = ("预警", "AWACS", "AEW", "RLD", "E-2", "E-3", "E-7", "A-50", "KJ-")


def _point(row: dict) -> tuple[float, float] | None:
    try:
        lat, lon = row["lat"], row["lon"]
        if type(lat) is bool or type(lon) is bool:
            return None
        lat, lon = float(lat), float(lon)
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 <= lat <= 85 and -180 <= lon <= 180):
        return None
    return lat, lon


def _distance_km(origin: tuple[float, float], target: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *target))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(hav)))


def _bearing_deg(origin: tuple[float, float], target: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *target))
    dlon = lon2 - lon1
    east = math.sin(dlon) * math.cos(lat2)
    north = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(east, north)) + 360) % 360


def _is_early_warning(row: dict) -> bool:
    label = f"{row.get('name', '')} {row.get('sub_type', '')}".upper()
    return any(marker in label for marker in _EARLY_WARNING_MARKERS)


def suspected_air_attack_axis(situation: dict, *, origin: tuple[float, float] | None = None) -> dict | None:
    """Return a bearing toward the sole observed enemy AEW contact, if present.

    A blue ship/facility nearest the contact is the default reference. The AWACS
    area rule passes its selected launch platform as ``origin`` instead.
    """
    contacts = situation.get("contacts", {}).get("air", [])
    if len(contacts) != 1 or not isinstance(contacts[0], dict):
        return None
    contact = contacts[0]
    target = _point(contact)
    if not target or not _is_early_warning(contact):
        return None

    reference_guid = ""
    if origin is None:
        units = situation.get("units", {})
        candidates = [row for group in ("navy", "army")
                      for row in units.get(group, []) if isinstance(row, dict) and _point(row)]
        if not candidates:
            return None
        reference = min(candidates, key=lambda row: _distance_km(_point(row), target))
        origin = _point(reference)
        reference_guid = str(reference.get("guid", ""))

    return {
        "status": "suspected",
        "basis": "sole_current_air_contact_is_enemy_early_warning",
        "contact_guid": str(contact.get("guid", "")),
        "contact_name": str(contact.get("name", "")),
        "reference_guid": reference_guid,
        "bearing_deg": round(_bearing_deg(origin, target), 1),
        "distance_km": round(_distance_km(origin, target), 1),
        "other_aircraft_confirmed": False,
    }
