"""Merge two unexecuted proposals without losing independent original calls.

The revised proposal has priority. Matching call IDs, mission names or existing
mission GUIDs replace the original call. Aircraft allocation conflicts also
prefer the revised call and are reported explicitly. This is not an executor or
validator: all returned calls still pass normal ownership/target/area checks.
"""

from __future__ import annotations

from copy import deepcopy


def _text(value: object) -> str:
    return str(value or "").strip()


def _guid(value: object) -> str:
    return _text(value).strip("{}").lower()


def _units(call: dict) -> set[str]:
    values = []
    for field in ("unit_guids", "escort_unit_guids"):
        rows = call.get(field) or []
        if isinstance(rows, list):
            values.extend(rows)
    return {_guid(value) for value in values if _guid(value)}


def _identity(call: dict) -> set[tuple[str, str]]:
    identities = set()
    for field in ("call_id", "mission_name", "existing_mission_guid"):
        value = (_guid(call.get(field)) if field == "existing_mission_guid"
                 else _text(call.get(field)))
        if value:
            identities.add((field, value))
    return identities


def merge_review_calls(original: list[dict], revised: list[dict]) -> tuple[list[dict], dict]:
    """Prefer revised calls and preserve original calls with no conflicts.

    Unit conflicts include main and escort assignments. We never repair or
    silently remove conflicts *within* the revised list; normal validation
    must report those. Returned data is detached from both input proposals.
    """
    merged = deepcopy(revised)
    identities = {item for call in revised for item in _identity(call)}
    used_units = {unit for call in revised for unit in _units(call)}
    report = {"preserved_call_ids": [], "replaced_call_ids": [],
              "conflicting_call_ids": []}
    for call in original:
        label = _text(call.get("call_id")) or _text(call.get("mission_name"))
        if _identity(call).intersection(identities):
            report["replaced_call_ids"].append(label)
            continue
        if _units(call).intersection(used_units):
            report["conflicting_call_ids"].append(label)
            continue
        merged.append(deepcopy(call))
        report["preserved_call_ids"].append(label)
        identities.update(_identity(call))
        used_units.update(_units(call))
    return merged, report
