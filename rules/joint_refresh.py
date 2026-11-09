"""Low-frequency joint commander refresh with immediate strategic invalidation."""

from __future__ import annotations

from rules.situation_memory import _point, nearest_blue_km, range_band


CONTACT_KINDS = ("air", "sea", "land", "submarine")
FORCE_KINDS = ("air", "navy", "army", "submarine")


def strategic_snapshot(situation: dict) -> dict:
    """Ignore tactical contact churn; preserve first/last threat class and force changes."""
    scenario = situation.get("scenario", {})
    units = situation.get("units", {})
    contacts = situation.get("contacts", {})
    threat_ranges = {}
    severity = {"unknown": 0, "beyond_300_km": 1, "within_300_km": 2,
                "within_150_km": 3, "within_75_km": 4}
    for kind in CONTACT_KINDS:
        bands = [range_band(nearest_blue_km(point, situation))
                 for row in contacts.get(kind, []) if (point := _point(row)) is not None]
        threat_ranges[kind] = max(bands, key=lambda band: severity[band]) if bands else None
    return {
        "side_guid": str(situation.get("side", {}).get("guid", "")),
        "scenario": str(scenario.get("initial_time") or scenario.get("start_time") or ""),
        "scenario_name": str(scenario.get("name", "")),
        "forces": {kind: sorted(str(row.get("guid", "")).strip("{}").lower()
                                for row in units.get(kind, []) if isinstance(row, dict))
                   for kind in FORCE_KINDS},
        "threat_present": {kind: bool(contacts.get(kind)) for kind in CONTACT_KINDS},
        # 距离段跨越才刷新联合指挥；细小位置变化只交给专业 Agent。
        "threat_ranges": threat_ranges,
    }


def refresh_reason(cache: dict | None, snapshot: dict, instruction: str,
                   now: float, interval_sec: float) -> str:
    if not cache:
        return "首次规划"
    if cache.get("instruction") != instruction:
        return "总体指令变化"
    previous = cache.get("snapshot") or {}
    if any(previous.get(key) != snapshot.get(key)
           for key in ("side_guid", "scenario", "scenario_name")):
        return "想定或推演方变化"
    if previous.get("forces") != snapshot.get("forces"):
        return "蓝方兵力变化"
    if previous.get("threat_present") != snapshot.get("threat_present"):
        return "敌方威胁类别出现或消失"
    if previous.get("threat_ranges") != snapshot.get("threat_ranges"):
        return "敌方威胁距离段变化"
    age = now - float(cache.get("at", -float("inf")))
    if age < 0 or age >= interval_sec:
        return "联合方案到达刷新间隔"
    return ""
