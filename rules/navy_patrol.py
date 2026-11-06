"""Sea patrol is available only for the opening planning round."""

from __future__ import annotations


def opening_allowed(state: dict) -> bool:
    return not bool(state.get("navy_patrol_opening_done", False))


def available_skills(service: str, catalog: list[dict], allowed: bool) -> list[dict]:
    if service == "navy" and not allowed:
        return [entry for entry in catalog if entry.get("skill_id") != "navy.sea_patrol"]
    return catalog


def suppress_late_calls(calls: list, allowed: bool) -> tuple[list, int]:
    if allowed:
        return calls, 0
    kept = [call for call in calls if not (isinstance(call, dict)
            and call.get("skill_id") == "navy.sea_patrol")]
    return kept, len(calls) - len(kept)
