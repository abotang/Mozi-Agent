"""Read current blue aircraft patrol and early-warning polygons from Mozi.

``ScenArea.missionList`` can lag behind a patrol area's reassignment. A linked
mission is therefore counted only when its current ``Mission`` detail still
points at the area. API read failures stop planning rather than silently
dropping an occupied part of the map. Invalid geometry is retained as an
active area with an error marker, so non-patrol tasks can still be planned.
"""

from __future__ import annotations

import json
import math
from urllib.parse import quote
from urllib.request import urlopen
from uuid import UUID


AREA_KINDS = {"405": "combat_patrol", "407": "early_warning"}


def _guid(value: object) -> str:
    try:
        return str(UUID(str(value).strip().strip("{}")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise RuntimeError(f"墨子区域或任务 GUID 无效：{value!r}") from exc


def _json(url: str) -> dict:
    with urlopen(url, timeout=12) as response:
        return json.load(response)


def _data(payload: dict, action: str) -> dict:
    if (not isinstance(payload, dict) or payload.get("code") != 0
            or not isinstance(payload.get("data"), dict)):
        raise RuntimeError(f"墨子{action}读取失败")
    return payload["data"]


def _points(area: dict) -> list[dict[str, float]]:
    rows = area.get("points")
    if not isinstance(rows, list) or len(rows) < 3:
        raise RuntimeError("墨子已关联巡逻区缺少可核对的多边形点")
    points = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("墨子巡逻区参考点格式无效")
        latitude, longitude = row.get("latitude"), row.get("longitude")
        if (type(latitude) not in (int, float)
                or type(longitude) not in (int, float)
                or not math.isfinite(latitude) or not math.isfinite(longitude)
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            raise RuntimeError("墨子巡逻区参考点坐标无效")
        points.append({"lat": float(latitude), "lon": float(longitude)})
    return points


def read_blue_patrol_areas(api_base: str, side_guid: str, *, get_json=None) -> list[dict]:
    """Return active air-CAP (405) and early-warning (407) areas only.

    Each item has ``guid``, ``name``, ``kind``, ``type_id``, ``points``,
    ``mission_guids``, ``mission_names`` and ``geometry_error``. Unlinked or
    stale areas are omitted. A mission-detail read fails; malformed active
    geometry remains visible with empty points for patrol-spacing validation.
    """
    base = str(api_base).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("墨子 API 地址必须是 HTTP(S)")
    side_id = _guid(side_guid)
    fetch = get_json or _json
    sides = _data(fetch(f"{base}/ScenArea?type=all"), "区域列表").get("sideAreas")
    if not isinstance(sides, list):
        raise RuntimeError("墨子区域列表缺少 sideAreas")
    side = next((row for row in sides if isinstance(row, dict)
                 and _guid(row.get("guid")) == side_id), None)
    if side is None or not isinstance(side.get("areaTypes"), list):
        raise RuntimeError("墨子区域列表缺少当前蓝方的 areaTypes")

    mission_cache: dict[str, dict] = {}
    result = []
    for group in side["areaTypes"]:
        if not isinstance(group, dict):
            continue
        type_id = str(group.get("id", ""))
        if type_id not in AREA_KINDS:
            continue
        areas = group.get("areas")
        if not isinstance(areas, list):
            raise RuntimeError(f"墨子 {type_id} 类区域缺少 areas 列表")
        for area in areas:
            if not isinstance(area, dict):
                raise RuntimeError("墨子巡逻区记录格式无效")
            area_id = _guid(area.get("id"))
            links = area.get("missionList")
            if not isinstance(links, list):
                raise RuntimeError(f"墨子区域 {area_id} 缺少 missionList")
            current = []
            for link in links:
                if not isinstance(link, dict):
                    raise RuntimeError(f"墨子区域 {area_id} 的任务关联格式无效")
                mission_id = _guid(link.get("id"))
                if mission_id not in mission_cache:
                    detail = _data(fetch(f"{base}/Mission?Guid={quote(mission_id)}"),
                                   f"任务 {mission_id} 详情")
                    if not isinstance(detail.get("missionBasicData"), dict):
                        raise RuntimeError(f"墨子任务 {mission_id} 缺少 missionBasicData")
                    mission_cache[mission_id] = detail
                detail = mission_cache[mission_id]
                basic = detail["missionBasicData"]
                if type_id == "405":
                    if (str(basic.get("classify") or "") != "空战巡逻"
                            and str(basic.get("typeName") or "") != "空战巡逻"):
                        continue  # 405 also contains ship and other patrols.
                    patrol = detail.get("patrolmissionData")
                    if not isinstance(patrol, dict):
                        raise RuntimeError(f"空战巡逻任务 {mission_id} 缺少 patrolmissionData")
                    current_id = patrol.get("PatrolArea_ID")
                else:
                    support = detail.get("supportmissionData")
                    if not isinstance(support, dict):
                        raise RuntimeError(f"预警任务 {mission_id} 缺少 supportmissionData")
                    current_id = support.get("area_ID")
                if current_id and _guid(current_id) == area_id:
                    current.append((mission_id, str(basic.get("name") or link.get("name") or "")))
            if not current:
                continue
            try:
                points = _points(area)
                geometry_error = None
            except RuntimeError as exc:
                points = []
                geometry_error = f"区域 {area_id} 几何不可核对：{exc}"
            result.append({
                "guid": area_id,
                "name": str(area.get("name") or ""),
                "kind": AREA_KINDS[type_id],
                "type_id": type_id,
                "points": points,
                "geometry_error": geometry_error,
                "mission_guids": [guid for guid, _ in current],
                "mission_names": [name for _, name in current],
            })
    return result
