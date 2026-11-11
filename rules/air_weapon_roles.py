"""Build weapon-role evidence from currently mounted aircraft loadouts.

Only positive-count weaponRecord entries of type=loadout are considered.
A failed read or malformed payload produces unknown capabilities, never a
positive strike claim. This does not estimate range or combat radius.
"""

from __future__ import annotations

import json
import math
from urllib.parse import quote
from urllib.request import urlopen


_SKILL_ROLE = {
    "air.air_strike": "air",
    "air.sea_strike": "sea",
    "air.land_strike": "land",
}
_AIR_MARKERS = ("空空导弹", "空对空导弹", "AIM-", "PL-", "R-27", "R-73", "METEOR", "MICA")
_SEA_MARKERS = ("反舰导弹", "空舰导弹", "鱼叉", "HARPOON", "LRASM", "NSM", "YJ-", "鹰击", "AGM-84L")
_LAND_MARKERS = ("空地导弹", "对地导弹", "激光制导炸弹", "JDAM", "GBU-", "MK-82", "MK-83",
                 "MK-84", "AGM-65", "AGM-88", "AGM-154", "AGM-158", "AGM-84H", "AGM-84K")


def role_for_skill(skill_id: str) -> str | None:
    return _SKILL_ROLE.get(skill_id)


def _weapon_roles(name: str) -> set[str]:
    label = name.upper()
    roles = set()
    if any(marker in label for marker in _AIR_MARKERS):
        roles.add("air")
    if any(marker in label for marker in _SEA_MARKERS):
        roles.add("sea")
    if any(marker in label for marker in _LAND_MARKERS):
        roles.add("land")
    return roles


def compatible_with_skill(unit: dict, skill_id: str, *, live: bool) -> bool:
    """Legacy offline examples may omit inventory; live input must prove it."""
    role = role_for_skill(skill_id)
    if role is None:
        return True
    capabilities = unit.get("strike_capabilities")
    if capabilities is None:
        return not live
    return isinstance(capabilities, list) and role in capabilities


def read_current_loadout(api_base: str, guid: str) -> dict:
    """Read one aircraft's current loadout; unknown never grants strike."""
    unknown = {"weapon_info_status": "unknown", "loadout_name": "",
               "strike_capabilities": []}
    try:
        url = f"{api_base.rstrip('/')}/unitWeaponInfo?guid={quote(guid)}"
        with urlopen(url, timeout=6) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or payload.get("code") != 0:
            return unknown
        rows = payload.get("data")
        if not isinstance(rows, list):
            return unknown
        loadouts = [row for row in rows if isinstance(row, dict)
                    and str(row.get("type", "")).lower() == "loadout"]
        if len(loadouts) != 1:
            return unknown
        loadout = loadouts[0]
        records = loadout.get("weaponRecord")
        if not isinstance(records, list):
            return unknown
        roles: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                return unknown
            count = record.get("curNum")
            if isinstance(count, bool):
                return unknown
            try:
                count_num = float(count)
            except (TypeError, ValueError):
                return unknown
            if not math.isfinite(count_num) or count_num < 0:
                return unknown
            if count_num > 0:
                roles.update(_weapon_roles(str(record.get("name") or "")))
        return {"weapon_info_status": "ok",
                "loadout_name": str(loadout.get("name") or "")[:120],
                "strike_capabilities": sorted(roles)}
    except Exception:
        return unknown

