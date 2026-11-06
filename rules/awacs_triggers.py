"""Deterministic AWACS agent wake-up and mission-update rules."""

from __future__ import annotations

from datetime import datetime, timedelta

from rules.awacs_area import _distance_km, _position, is_awacs


SKILL_ID = "air.early_warning"
THREAT_KM = 300.0


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("T", " "))
    except (TypeError, ValueError):
        return None


def _guid(value):
    return str(value or "").strip("{}").lower()


def opening_signature(situation: dict) -> list[str]:
    contacts = [str(row.get("guid")) for row in situation["contacts"].get("air", [])]
    units = [f"{row.get('guid')}:{row.get('in_facility')}" for row in
             situation["units"].get("air", []) if is_awacs(row)]
    return sorted(contacts + units)


def next_trigger(situation: dict, state: dict) -> dict:
    """Update flight observations, then return one bounded event per polling cycle."""
    flights = state.setdefault("awacs_flights", {})
    aircraft = {_guid(row.get("guid")): row for row in situation["units"].get("air", [])
                if is_awacs(row)}
    missions = [row for row in situation.get("existing_missions", [])
                if row.get("skill_id") == SKILL_ID]
    contacts = [(row, _position(row)) for row in situation["contacts"].get("air", [])]
    contacts = [(row, point) for row, point in contacts if point is not None]
    for mission in missions:
        guid = _guid(mission.get("guid"))
        flight = flights.setdefault(guid, {})
        for unit_guid in mission.get("unit_guids", []):
            unit = aircraft.get(_guid(unit_guid))
            if unit and unit.get("airborne") is True:
                flight["airborne_seen"] = True

    if not missions:
        signature = opening_signature(situation)
        if aircraft and state.get("awacs_opening_attempt") != signature and not state.get("uncertain_awacs"):
            return {"kind": "opening", "mode": "threat" if contacts else "local"}
        return {"kind": "hold"}

    occupied = {_guid(guid) for row in missions for guid in row.get("unit_guids", [])}
    now = _time(situation["scenario"].get("start_time"))
    for mission in missions:
        guid = _guid(mission.get("guid"))
        flight = flights.setdefault(guid, {})
        assigned = [aircraft.get(_guid(value)) for value in mission.get("unit_guids", [])]
        assigned = [unit for unit in assigned if unit]
        old_unit = assigned[0] if assigned else None
        base = {"mission_guid": mission.get("guid"), "mission_name": mission.get("name"),
                "unit_guid": old_unit.get("guid") if old_unit else "",
                "area_guid": mission.get("area_guid", ""),
                "area_points": mission.get("area_points", []),
                "area_remark": mission.get("area_remark", ""),
                "deployment": mission.get("deployment", {})}
        if old_unit and flight.get("airborne_seen") and old_unit.get("in_facility") is True:
            spare = next((row for key, row in aircraft.items()
                          if key not in occupied and row.get("in_facility") is True), None)
            if spare and base["area_guid"] and base["area_points"]:
                return {**base, "kind": "relief", "replacement_guid": spare["guid"]}
        if old_unit and old_unit.get("airborne") is True and not flight.get("retreat_pending"):
            position = _position(old_unit)
            near = [(row, point, _distance_km(position, point)) for row, point in contacts] if position else []
            near = [(row, point, distance) for row, point, distance in near if distance <= THREAT_KM]
            if near and base["area_points"]:
                enemy, point, distance = min(near, key=lambda item: item[2])
                return {**base, "kind": "threat_retreat",
                        "enemy_guid": enemy["guid"], "enemy_position": {"lat": point[0], "lon": point[1]},
                        "enemy_distance_km": round(distance, 1)}
        if flight.get("retreat_pending") and old_unit and _position(old_unit):
            if all(_distance_km(_position(old_unit), point) > THREAT_KM for _, point in contacts):
                flight["retreat_pending"] = False
        created = _time(mission.get("created_at_sim") or situation["scenario"].get("initial_time"))
        if (mission.get("deployment", {}).get("mode") == "local" and contacts and now and created
                and now - created >= timedelta(minutes=20) and old_unit):
            return {**base, "kind": "local_to_threat"}
    return {"kind": "hold"}


def prepare_area(call, trigger: dict):
    if trigger["kind"] == "relief":
        return {"name": f"{call.mission_name}-预警跑马道",
                "existing_area_guid": trigger["area_guid"],
                "points": trigger["area_points"],
                "remark": trigger.get("area_remark") or "预警机换班，沿用原区域",
                "deployment": trigger.get("deployment", {})}
    return None


def validate_call(call, trigger: dict, previous_area: dict | None = None):
    kind = trigger.get("kind", "hold")
    if kind == "hold":
        raise ValueError("预警机 Agent 未被规则触发，不能调用侦察 Skill")
    if kind == "opening":
        if call.existing_mission_guid:
            raise ValueError("预警开局必须新建任务")
        expected = trigger.get("mode")
        actual = call.area.get("deployment", {}).get("mode")
        if actual != expected:
            raise ValueError(f"预警开局区域必须为 {expected} 模式")
        return
    if _guid(call.existing_mission_guid) != _guid(trigger.get("mission_guid")) or call.mission_name != trigger.get("mission_name"):
        raise ValueError("预警更新必须复用规则指定的任务名称和 GUID")
    if kind == "relief":
        if (_guid(call.unit_guids[0]) != _guid(trigger.get("replacement_guid"))
                or _guid(call.parameters.get("relieved_unit_guid")) != _guid(trigger.get("unit_guid"))
                or _guid(call.area.get("existing_area_guid")) != _guid(trigger.get("area_guid"))):
            raise ValueError("预警换班必须移除返航飞机，分配备用机并沿用原区域")
        return
    if _guid(call.unit_guids[0]) != _guid(trigger.get("unit_guid")):
        raise ValueError("预警区域更新不能更换执行飞机")
    deployment = call.area.get("deployment", {})
    if deployment.get("mode") != "threat":
        raise ValueError("敌情触发的预警区域必须依据进攻轴向")
    if kind == "threat_retreat":
        old_forward = trigger.get("deployment", {}).get("forward_km", 0)
        if deployment.get("forward_km", 0) >= old_forward:
            raise ValueError("预警回撤必须向起飞平台或其后方移动")
        from rules.awacs_deployment import area_center
        old_center = _position(area_center(previous_area or {"points": trigger.get("area_points", [])}) or {})
        new_center = _position(area_center(call.area) or {})
        enemy = _position(trigger.get("enemy_position", {}))
        if old_center and new_center and enemy and _distance_km(new_center, enemy) <= _distance_km(old_center, enemy):
            raise ValueError("预警回撤后的区域中心必须远离触发敌机")


def record_success(state: dict, call, mission_guid: str):
    flight = state.setdefault("awacs_flights", {}).setdefault(_guid(mission_guid), {})
    trigger = state.get("last_awacs_trigger", {})
    if trigger.get("kind") == "threat_retreat":
        flight["retreat_pending"] = True
    if trigger.get("kind") == "relief":
        flight["airborne_seen"] = False
        flight["retreat_pending"] = False
