"""把 Agent 方案中含义明确的格式变体转成任务 Skill 的标准协议。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CALL_ALIASES = {
    "callId": "call_id", "skillId": "skill_id", "missionName": "mission_name",
    "unitGuids": "unit_guids", "escortUnitGuids": "escort_unit_guids",
    "targetContactGuids": "target_contact_guids", "loadoutGuid": "loadout_guid",
    "target_guid": "target_contact_guids", "target_guids": "target_contact_guids",
    "target_contact_guid": "target_contact_guids", "targetContactGuid": "target_contact_guids",
    "existingMissionGuid": "existing_mission_guid",
}


def _alias(row: dict[str, Any], old: str, new: str, label: str) -> None:
    if old not in row:
        return
    if new in row and row[new] != row[old]:
        raise ValueError(f"{label} 同时包含冲突字段 {old} 和 {new}")
    row[new] = row.pop(old)


def _points(points: Any, label: str) -> Any:
    if not isinstance(points, list):
        return points
    for index, point in enumerate(points, 1):
        if not isinstance(point, dict):
            continue  # 后续规则校验会指出无效坐标。
        _alias(point, "Latitude", "lat", f"{label}[{index}]")
        _alias(point, "Longitude", "lon", f"{label}[{index}]")
    return points


def normalize_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """只处理等价写法；不补造单位、目标、经纬度或任务区域。"""
    if not isinstance(raw, dict):
        raise ValueError("任务方案必须是 JSON 对象")
    plan = deepcopy(raw)
    calls = plan.get("skill_calls")
    if not isinstance(calls, list):
        return plan  # 保留原值交给规则校验说明错误。
    for index, call in enumerate(calls, 1):
        if not isinstance(call, dict):
            raise ValueError(f"skill_calls[{index}] 必须是对象")
        label = f"skill_calls[{index}]"
        # Compare target aliases after scalar/list normalization. This accepts
        # target_guid="G" together with target_contact_guids=["G"], but never
        # chooses one conflicting target over another or resolves target names.
        target_fields = ["target_contact_guids"] + [
            old for old, new in CALL_ALIASES.items() if new == "target_contact_guids"]
        for key in target_fields:
            if isinstance(call.get(key), str):
                call[key] = [call[key]]
            elif key in call and call[key] is None:
                call[key] = []
        for old, new in CALL_ALIASES.items():
            _alias(call, old, new, label)
        for key in ("unit_guids", "escort_unit_guids", "target_contact_guids"):
            if isinstance(call.get(key), str):
                call[key] = [call[key]]
            elif key in call and call[key] is None:
                call[key] = []
        if "parameters" in call and call["parameters"] is None:
            call["parameters"] = {}

        area = call.get("area")
        if isinstance(area, list):
            area = {"points": area}
            call["area"] = area
        if isinstance(area, dict):
            _alias(area, "existingAreaGuid", "existing_area_guid", f"{label}.area")
            _alias(area, "attackBearingDeg", "attack_bearing_deg", f"{label}.area")
            _alias(area, "forwardKm", "forward_km", f"{label}.area")
            _points(area.get("points"), f"{label}.area.points")

        routes = call.get("routes")
        if isinstance(routes, dict):
            _alias(routes, "onStation", "on_station", f"{label}.routes")
            for key in ("outbound", "on_station"):
                route = routes.get(key)
                if isinstance(route, list):
                    route = {"points": route} if route else None
                    routes[key] = route
                if isinstance(route, dict):
                    _alias(route, "existingRouteGuid", "existing_route_guid",
                           f"{label}.routes.{key}")
                    _points(route.get("points"), f"{label}.routes.{key}.points")
    return plan
