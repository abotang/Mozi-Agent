"""Keep AWACS task count and station spacing aligned with the observed theater."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re

from rules.awacs_area import _distance_km, _position
from rules.air_patrol_update import CAP_SKILL, saved_current_area


AWACS_SKILL_ID = "air.early_warning"
MIN_SEPARATION_KM = 90.0
MAX_SEPARATION_KM = 250.0
SPAN_FRACTION = 0.25


def area_center(area: dict | None) -> dict[str, float] | None:
    points = area.get("points") if isinstance(area, dict) else None
    if not isinstance(points, list) or len(points) < 3:
        return None
    positions = [_position(point) for point in points if isinstance(point, dict)]
    if len(positions) != len(points) or any(point is None for point in positions):
        return None
    lat = sum(point[0] for point in positions) / len(positions)
    longitude = [math.radians(point[1]) for point in positions]
    lon = math.degrees(math.atan2(sum(math.sin(value) for value in longitude),
                                  sum(math.cos(value) for value in longitude)))
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


def battlefield_span_km(situation: dict) -> float:
    """Observed blue launch platforms and contacts define the current extent."""
    points = []
    for group in situation.get("contacts", {}).values():
        points.extend(point for row in group if isinstance(row, dict)
                      if (point := _position(row)) is not None)
    for service in ("army", "navy"):
        for row in situation.get("units", {}).get(service, []):
            if not isinstance(row, dict):
                continue
            label = f"{row.get('name', '')} {row.get('sub_type', '')}".lower()
            if not any(word in label for word in
                       ("机场", "航空基地", "空军基地", "airport", "airbase", "航母", "carrier", "cvn")):
                continue
            if (point := _position(row)) is not None:
                points.append(point)
    return max((_distance_km(a, b) for index, a in enumerate(points)
                for b in points[index + 1:]), default=0.0)


def minimum_separation_km(situation: dict) -> float:
    return round(min(MAX_SEPARATION_KM,
                     max(MIN_SEPARATION_KM, battlefield_span_km(situation) * SPAN_FRACTION)), 1)


def enrich_existing_mission(name: str, record: dict) -> dict:
    """Recover the station and assigned aircraft from this project's saved config."""
    item = {"name": name, "guid": record.get("guid"), "skill_id": record.get("skill_id")}
    if record.get("skill_id") == CAP_SKILL:
        item["current_area"] = saved_current_area(record)
        return item
    if record.get("skill_id") != AWACS_SKILL_ID:
        return item
    try:
        report = Path(record["report"])
        config = report.parent.parent / "configs" / report.name
        data = json.loads(config.read_text(encoding="utf-8-sig"))
        task = data["tasks"]["airEarlyWarning"]
        item["unit_guids"] = task.get("unitGuids", [])
        item["area_center"] = area_center(task.get("area"))
        item["area_points"] = (task.get("area") or {}).get("points", [])
        remark = str((task.get("area") or {}).get("remark", ""))
        item["area_remark"] = remark
        match = re.search(r"前推(-?\d+(?:\.\d+)?)km", remark)
        platform = re.search(r"最近起飞平台:[^(]+\(([^)]+)\)", remark)
        recovered_deployment = {
            "mode": "local" if "无已知进攻轴向" in remark else "threat",
            "forward_km": float(match.group(1)) if match else 0,
            "platform_guid": platform.group(1) if platform else "",
        }
        if report.is_file():
            report_data = json.loads(report.read_text(encoding="utf-8-sig"))
            for row in report_data.get("tasks", []):
                if str(row.get("missionGuid", "")).strip("{}").lower() == str(record.get("guid", "")).strip("{}").lower():
                    item["area_guid"] = (row.get("area") or {}).get("guid", "")
                    break
    except (KeyError, TypeError, OSError, ValueError):
        item["area_center"] = None
    item["deployment"] = record.get("deployment") or locals().get("recovered_deployment", {})
    item["created_at_sim"] = record.get("created_at_sim", "")
    return item


def validate_deployments(calls: list, situation: dict) -> None:
    new = [call for call in calls if call.skill_id == AWACS_SKILL_ID]
    if not new:
        return
    existing = [row for row in situation.get("existing_missions", [])
                if isinstance(row, dict) and row.get("skill_id") == AWACS_SKILL_ID
                and not any(call.existing_mission_guid
                            and str(row.get("guid", "")).strip("{}").lower()
                            == call.existing_mission_guid.strip("{}").lower()
                            for call in new)]
    if len(existing) + len(new) > 2:
        raise ValueError("预警任务最多同时安排两架预警机；已有任务应先保持")
    previous_units = {str(guid).strip("{}").lower() for row in existing
                      for guid in row.get("unit_guids", []) if isinstance(guid, str)}
    for call in new:
        if any(guid.lower() in previous_units for guid in call.unit_guids):
            raise ValueError(f"{call.call_id} 预警机已在现有预警任务中，不能重复出动")
    if len(existing) + len(new) < 2:
        return
    for call in new:
        if len(call.reason.strip()) < 12:
            raise ValueError(f"{call.call_id} 第二架预警机须在 reason 说明大范围态势与独立覆盖需求")
    stations = [(str(row.get("name", "已有预警任务")), _position(row.get("area_center", {})))
                for row in existing]
    stations.extend((call.mission_name, _position(area_center(call.area) or {})) for call in new)
    if any(point is None for _, point in stations):
        raise ValueError("已有预警任务缺少可核对的区域中心，不能安全新增第二架预警机")
    required = minimum_separation_km(situation)
    for index, (left_name, left) in enumerate(stations):
        for right_name, right in stations[index + 1:]:
            actual = _distance_km(left, right)
            if actual < required:
                raise ValueError(f"预警机间距不足：{left_name} 与 {right_name} 的区域中心相距"
                                 f" {actual:.1f} km，当前态势至少需要 {required:g} km")
