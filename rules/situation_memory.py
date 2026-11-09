"""确定性态势记忆：压缩实时轨迹与近几轮方案，不调用第二个模型。

原始监控日志供人审计，不回灌模型。本模块只保留当前想定的少量位置采样、
新建/丢失/距离段变化事件，以及已通过本地校验的方案摘要。
历史接触只用于趋势分析；真正分配打击目标仍必须使用本轮 contacts GUID。
"""

from __future__ import annotations

from datetime import datetime
import json
from math import asin, atan2, cos, degrees, radians, sin, sqrt


MAX_TRACKS = 80
MAX_SAMPLES = 10
MAX_EVENTS = 30
MAX_PLANS = 6
MAX_OUTCOMES = 16
GROUPS = {"air", "sea", "land", "submarine"}
OWNER_GROUPS = {
    "air": {"air", "sea", "land"},
    "awacs": {"air"},
    "navy": {"sea", "air"},
    "army": {"land", "air"},
}


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("T", " "))
    except (TypeError, ValueError):
        return None


def scenario_rewound(memory: dict | None, situation: dict) -> bool:
    """同一想定的仿真时间倒退视作新一局，旧态势不得继续影响指挥。"""
    if not isinstance(memory, dict):
        return False
    current = _time((situation.get("scenario") or {}).get("start_time"))
    previous = _time(memory.get("last_sim_time"))
    return bool(current and previous and current < previous)


def _point(row):
    try:
        lat, lon = float(row.get("lat")), float(row.get("lon"))
    except (TypeError, ValueError, AttributeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def distance_km(a, b) -> float:
    """球面距离；只用于趋势和触发，不作为武器射程判据。"""
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    value = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 12742.0 * asin(min(1.0, sqrt(value)))


def bearing_deg(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    delta = lon2 - lon1
    y = sin(delta) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta)
    return (degrees(atan2(y, x)) + 360) % 360


def protected_positions(situation: dict) -> list[tuple[float, float]]:
    """优先以水面舰艇/陆基设施衡量威胁距离；没有时退化为己方飞机。"""
    units = situation.get("units") or {}
    primary = [_point(row) for kind in ("navy", "army") for row in units.get(kind, [])]
    primary = [point for point in primary if point is not None]
    if primary:
        return primary
    return [point for row in units.get("air", []) if (point := _point(row)) is not None]


def nearest_blue_km(point, situation: dict) -> float | None:
    positions = protected_positions(situation)
    return min((distance_km(point, blue) for blue in positions), default=None)


def range_band(distance: float | None) -> str:
    if distance is None:
        return "unknown"
    if distance <= 75:
        return "within_75_km"
    if distance <= 150:
        return "within_150_km"
    if distance <= 300:
        return "within_300_km"
    return "beyond_300_km"


def _event(memory: dict, when: str, kind: str, group: str, name: str, detail: str) -> None:
    memory["events"].append({"time": when, "type": kind, "group": group,
                             "name": name[:80], "detail": detail[:120]})
    memory["events"] = memory["events"][-MAX_EVENTS:]


def update_memory(memory: dict | None, situation: dict) -> dict:
    """每次轮询更新。想定时间倒退时丢弃旧轨迹，防止重启想定混入上一局。"""
    memory = memory if isinstance(memory, dict) else {}
    when = str(situation.get("scenario", {}).get("start_time") or "")
    if scenario_rewound(memory, situation):
        memory = {}
    tracks = memory.setdefault("tracks", {})
    memory.setdefault("events", [])
    memory.setdefault("plans", [])
    memory.setdefault("outcomes", [])
    seen = set()
    for group, rows in (situation.get("contacts") or {}).items():
        if group not in GROUPS:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            guid = str(row.get("guid") or "").strip().strip("{}").lower()
            if not guid:
                continue
            seen.add(guid)
            name = str(row.get("name") or guid)[:80]
            track = tracks.get(guid)
            if not isinstance(track, dict):
                track = {"group": group, "name": name, "samples": [], "active": False}
                tracks[guid] = track
                _event(memory, when, "new_contact", group, name, "首次进入蓝方当前接触列表")
            elif not track.get("active"):
                _event(memory, when, "reacquired", group, name, "重新进入蓝方当前接触列表")
            track.update(group=group, name=name, active=True, last_seen=when)
            point = _point(row)
            if point is None:
                continue
            nearest = nearest_blue_km(point, situation)
            sample = {"time": when, "lat": round(point[0], 5), "lon": round(point[1], 5),
                      "nearest_blue_km": round(nearest, 1) if nearest is not None else None}
            samples = track.setdefault("samples", [])
            previous = samples[-1] if samples else None
            band_changed = False
            if previous:
                before = previous.get("nearest_blue_km")
                old_band, new_band = range_band(before), range_band(nearest)
                band_changed = old_band != new_band and "unknown" not in (old_band, new_band)
                if band_changed:
                    direction = "接近" if nearest < before else "远离"
                    _event(memory, when, "range_band", group, name,
                           f"距最近蓝方舰艇/设施{direction}，{old_band} → {new_band}")
            elapsed = (_time(when) - _time(previous["time"])).total_seconds() if previous and _time(when) and _time(previous["time"]) else 0
            moved = distance_km((previous["lat"], previous["lon"]), point) if previous else 0
            nearest_changed = (previous and previous.get("nearest_blue_km") is not None
                               and nearest is not None
                               and abs(previous["nearest_blue_km"] - nearest) >= 10)
            # 只采样有意义的变化，或每 2 分钟留一个时间锚点；固定上限防止上下文增长。
            if not previous or moved >= 2 or elapsed >= 120 or nearest_changed or band_changed:
                samples.append(sample)
                track["samples"] = samples[-MAX_SAMPLES:]
    for guid, track in tracks.items():
        if guid not in seen and track.get("active"):
            track["active"] = False
            _event(memory, when, "lost_contact", track.get("group", ""),
                   track.get("name", guid), "已不在蓝方当前接触列表；不得作为新打击目标")
    if len(tracks) > MAX_TRACKS:
        ordered = sorted(tracks, key=lambda key: (bool(tracks[key].get("active")),
                                                  str(tracks[key].get("last_seen", ""))), reverse=True)
        memory["tracks"] = {key: tracks[key] for key in ordered[:MAX_TRACKS]}
    memory["last_sim_time"] = when
    return memory


def record_plan(memory: dict, plan: dict, when: str) -> None:
    """只记录通过校验的方案摘要；是否真正创建以 existing_missions 为准。"""
    joint = plan.get("joint_directive") or {}
    proposals = plan.get("service_proposals") or {}
    services = {owner: str(value.get("summary") or "")[:150]
                for owner, value in proposals.items() if isinstance(value, dict)}
    tasks = []
    for call in (plan.get("skill_calls") or [])[:12]:
        if not isinstance(call, dict):
            continue
        skill = str(call.get("skill_id") or "")
        owner = "awacs" if skill == "air.early_warning" else skill.split(".", 1)[0]
        tasks.append({"service": owner, "skill": skill[:60],
                      "name": str(call.get("mission_name") or "")[:80]})
    entry = {"time": when, "joint_assessment": str(joint.get("situation_assessment") or "")[:180],
             "joint_effort": str(joint.get("main_effort") or "")[:180],
             "services": services, "proposed_tasks": tasks}
    plans = memory.setdefault("plans", [])
    signature = json.dumps({key: value for key, value in entry.items() if key != "time"},
                           ensure_ascii=False, sort_keys=True)
    if plans and plans[-1].get("signature") == signature:
        plans[-1]["last_time"] = when
        plans[-1]["repeats"] = int(plans[-1].get("repeats", 1)) + 1
    else:
        entry["signature"] = signature
        plans.append(entry)
        memory["plans"] = plans[-MAX_PLANS:]


def record_dispatch_outcome(memory: dict, when: str, mission_name: str,
                            skill_id: str, status: str, detail: str = "") -> None:
    """只记录任务脚本的创建/更新结果，不推断战斗效果或目标毁伤。"""
    if status not in {"created", "updated", "failed", "uncertain"}:
        raise ValueError("未知任务下发结果")
    owner = "awacs" if skill_id == "air.early_warning" else skill_id.split(".", 1)[0]
    entry = {"time": when, "service": owner, "mission_name": mission_name[:80],
             "skill_id": skill_id[:60], "status": status, "detail": detail[:120]}
    outcomes = memory.setdefault("outcomes", [])
    outcomes.append(entry)
    memory["outcomes"] = outcomes[-MAX_OUTCOMES:]


def _track_brief(guid: str, track: dict) -> dict:
    samples = track.get("samples") or []
    first, last = samples[0], samples[-1]
    start, end = (first["lat"], first["lon"]), (last["lat"], last["lon"])
    span = (_time(last["time"]) - _time(first["time"])).total_seconds() / 60 if _time(first["time"]) and _time(last["time"]) else 0
    before, after = first.get("nearest_blue_km"), last.get("nearest_blue_km")
    approach = round(before - after, 1) if before is not None and after is not None else None
    return {"guid": guid, "name": track.get("name", ""), "group": track.get("group", ""),
            "from": {"lat": first["lat"], "lon": first["lon"], "time": first["time"]},
            "to": {"lat": last["lat"], "lon": last["lon"], "time": last["time"]},
            "observed_minutes": round(span, 1), "moved_km": round(distance_km(start, end), 1),
            "course_deg": round(bearing_deg(start, end)) if start != end else None,
            "nearest_blue_km": after, "approach_km": approach,
            "approach_kmh": round(approach * 60 / span, 1) if approach is not None and span > 0 else None}


def memory_brief(memory: dict, owner: str | None = None) -> dict:
    """给 LLM 的定长视图；正的 approach_km 表示接近蓝方。"""
    allowed = OWNER_GROUPS.get(owner, GROUPS)
    tracks = [(guid, track) for guid, track in (memory.get("tracks") or {}).items()
              if isinstance(track, dict) and track.get("active") and track.get("group") in allowed
              and track.get("samples")]
    tracks.sort(key=lambda pair: (pair[1]["samples"][-1].get("nearest_blue_km") is None,
                                  pair[1]["samples"][-1].get("nearest_blue_km")
                                  if pair[1]["samples"][-1].get("nearest_blue_km") is not None
                                  else float("inf")))
    events = [event for event in (memory.get("events") or [])
              if isinstance(event, dict) and event.get("group") in allowed][-6:]
    plans = []
    for item in (memory.get("plans") or [])[-3:]:
        if not isinstance(item, dict):
            continue
        row = {"time": item.get("time"), "last_time": item.get("last_time", item.get("time")),
               "joint_effort": item.get("joint_effort", ""), "repeats": item.get("repeats", 1)}
        if owner is None:
            row["joint_assessment"] = item.get("joint_assessment", "")
            row["services"] = item.get("services", {})
            row["proposed_tasks"] = item.get("proposed_tasks", [])
        else:
            row["service_summary"] = (item.get("services") or {}).get(owner, "")
            row["proposed_tasks"] = [task for task in item.get("proposed_tasks", [])
                                     if task.get("service") == owner]
        plans.append(row)
    return {"last_sim_time": memory.get("last_sim_time", ""),
            "active_contact_trends": [_track_brief(guid, track) for guid, track in tracks[:12 if owner is None else 7]],
            "recent_events": events, "recent_plans_not_execution_results": plans,
            "recent_task_dispatch_outcomes_not_combat_effects": [
                item for item in memory.get("outcomes", [])
                if owner is None or item.get("service") == owner][-8 if owner is None else -5:]}


def filter_brief(brief: dict, owner: str) -> dict:
    """专业 Agent 只看相关接触和本军种方案，避免重复消耗上下文。"""
    allowed = OWNER_GROUPS.get(owner, GROUPS)
    plans = []
    for item in (brief.get("recent_plans_not_execution_results") or [])[-3:]:
        plans.append({"time": item.get("time"), "last_time": item.get("last_time"),
                      "joint_effort": item.get("joint_effort", ""),
                      "service_summary": (item.get("services") or {}).get(owner, ""),
                      "proposed_tasks": [task for task in item.get("proposed_tasks", [])
                                         if task.get("service") == owner]})
    return {"last_sim_time": brief.get("last_sim_time", ""),
            "active_contact_trends": [item for item in brief.get("active_contact_trends", [])
                                      if item.get("group") in allowed][:7],
            "recent_events": [item for item in brief.get("recent_events", [])
                              if item.get("group") in allowed][-6:],
            "recent_plans_not_execution_results": plans,
            "recent_task_dispatch_outcomes_not_combat_effects": [
                item for item in brief.get("recent_task_dispatch_outcomes_not_combat_effects", [])
                if item.get("service") == owner][-5:]}
