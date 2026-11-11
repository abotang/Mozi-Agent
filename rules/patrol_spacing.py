"""Keep active CAP and AWACS patrol polygons separated before dispatch.

Only proposed patrol areas are checked against current active areas and each
other. Existing violations that a proposal does not touch are reported in the
advisory but never block unrelated strike tasks. Distances are edge-to-edge,
including intersection and full containment; center distance is insufficient.
"""

from __future__ import annotations

import math
from typing import Any


EARTH_RADIUS_KM = 6371.0088
CAP_TYPE_ID = "405"
AWACS_TYPE_ID = "407"
MIN_CAP_CAP_GAP_KM = 160.0
MIN_CAP_AWACS_GAP_KM = 50.0
MIN_AWACS_AWACS_GAP_KM = 90.0
MIN_AWACS_REAR_OFFSET_KM = 30.0
MIN_AWACS_REAR_VERTEX_FRACTION = 2 / 3
MAX_CONSISTENT_AXIS_DEVIATION_DEG = 30.0
MAX_SAME_SECTOR_DEVIATION_DEG = 60.0
DEFAULT_MIN_GAPS_KM = {
    "405-405": MIN_CAP_CAP_GAP_KM,
    "405-407": MIN_CAP_AWACS_GAP_KM,
    "407-407": MIN_AWACS_AWACS_GAP_KM,
}
_SKILL_TYPES = {"air.combat_patrol": CAP_TYPE_ID,
                "air.early_warning": AWACS_TYPE_ID}


def _guid(value: object) -> str:
    return str(value or "").strip().strip("{}").lower()


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat, lon = value.get("lat"), value.get("lon")
    if (type(lat) not in (int, float) or type(lon) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
        return None
    return float(lat), float(lon)


def _polygon(raw: object) -> list[tuple[float, float]] | None:
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    points = [_point(value) for value in raw]
    if any(value is None for value in points):
        return None
    if points[0] == points[-1]:
        points.pop()
    return points if len(set(points)) >= 3 else None


def _existing(existing_areas: object) -> list[dict[str, Any]]:
    if not isinstance(existing_areas, list):
        raise ValueError("无法核对当前巡逻区：existing_areas 必须是已读取的区域列表")
    zones = []
    for row in existing_areas:
        if not isinstance(row, dict) or str(row.get("type_id")) not in (CAP_TYPE_ID, AWACS_TYPE_ID):
            continue
        links = row.get("mission_guids")
        if not isinstance(links, list):
            raise ValueError(f"无法核对当前巡逻区：{row.get('name', '未知区域')} 缺少任务关联列表")
        zones.append({
            "guid": _guid(row.get("guid")),
            "name": str(row.get("name") or "未命名区域"),
            "type_id": str(row["type_id"]),
            "points": _polygon(row.get("points")),
            "mission_guids": {_guid(value) for value in links if _guid(value)},
            "platform_guid": _guid(row.get("platform_guid")),
        })
    return zones


def _minimums(overrides: dict[str, float] | None) -> dict[str, float]:
    values = dict(DEFAULT_MIN_GAPS_KM)
    if overrides is None:
        return values
    if not isinstance(overrides, dict) or set(overrides) - set(values):
        raise ValueError("巡逻区净距配置只能覆盖 405-405、405-407、407-407")
    for key, value in overrides.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"巡逻区净距 {key} 必须是非负有限公里数")
        values[key] = float(value)
    return values


def _project(polygons: tuple[list[tuple[float, float]], ...]) -> list[list[tuple[float, float]]]:
    points = [point for polygon in polygons for point in polygon]
    latitude = sum(point[0] for point in points) / len(points)
    anchor = points[0][1]
    scale = EARTH_RADIUS_KM * math.pi / 180
    east_scale = scale * math.cos(math.radians(latitude))
    return [[(east_scale * ((lon - anchor + 180) % 360 - 180), scale * lat)
             for lat, lon in polygon] for polygon in polygons]


def _edges(polygon: list[tuple[float, float]]):
    for index, point in enumerate(polygon):
        yield point, polygon[(index + 1) % len(polygon)]


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-18:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = max(0.0, min(1.0,
                              ((point[0] - start[0]) * dx +
                               (point[1] - start[1]) * dy) / length_sq))
    return math.hypot(point[0] - start[0] - projection * dx,
                      point[1] - start[1] - projection * dy)


def _cross(start, end, point) -> float:
    return ((end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0]))


def _segments_intersect(a, b, c, d) -> bool:
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    tolerance = 1e-8
    if ((ab_c > tolerance and ab_d < -tolerance or
         ab_c < -tolerance and ab_d > tolerance) and
            (cd_a > tolerance and cd_b < -tolerance or
             cd_a < -tolerance and cd_b > tolerance)):
        return True
    return any((abs(cross) <= tolerance and
                _point_segment_distance(point, start, end) <= 1e-7)
               for cross, point, start, end in
               ((ab_c, c, a, b), (ab_d, d, a, b),
                (cd_a, a, c, d), (cd_b, b, c, d)))


def _inside(point, polygon) -> bool:
    if any(_point_segment_distance(point, start, end) <= 1e-7
           for start, end in _edges(polygon)):
        return True
    x, y = point
    enclosed = False
    for start, end in _edges(polygon):
        if (start[1] > y) != (end[1] > y):
            at_x = start[0] + (end[0] - start[0]) * (y - start[1]) / (end[1] - start[1])
            if x < at_x:
                enclosed = not enclosed
    return enclosed


def _boundary_gap_km(left: list[tuple[float, float]],
                     right: list[tuple[float, float]]) -> float:
    a, b = _project((left, right))
    if _inside(a[0], b) or _inside(b[0], a):
        return 0.0
    minimum = float("inf")
    for start_a, end_a in _edges(a):
        for start_b, end_b in _edges(b):
            if _segments_intersect(start_a, end_a, start_b, end_b):
                return 0.0
            minimum = min(minimum,
                          _point_segment_distance(start_a, start_b, end_b),
                          _point_segment_distance(end_a, start_b, end_b),
                          _point_segment_distance(start_b, start_a, end_a),
                          _point_segment_distance(end_b, start_a, end_a))
    return minimum


def _gap_key(left: dict, right: dict) -> str:
    return "-".join(sorted((left["type_id"], right["type_id"])))


def _violation(left: dict, right: dict, minimums: dict[str, float]) -> dict | None:
    required = minimums[_gap_key(left, right)]
    actual = _boundary_gap_km(left["points"], right["points"])
    if actual + 1e-6 >= required:
        return None
    return {"left": left["name"], "right": right["name"],
            "actual_km": round(actual, 1), "required_km": required,
            "overlap": actual <= 1e-6}


def _center(points: list[tuple[float, float]]) -> tuple[float, float]:
    latitude = sum(point[0] for point in points) / len(points)
    longitudes = [math.radians(point[1]) for point in points]
    longitude = math.degrees(math.atan2(sum(math.sin(value) for value in longitudes),
                                        sum(math.cos(value) for value in longitudes)))
    return latitude, longitude


def _distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, left)
    lat2, lon2 = map(math.radians, right)
    dlat = lat2 - lat1
    dlon = (lon2 - lon1 + math.pi) % (2 * math.pi) - math.pi
    haversine = (math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2)
                 * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(haversine)))


def _bearing_deg(origin: tuple[float, float], point: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, point)
    dlon = (lon2 - lon1 + math.pi) % (2 * math.pi) - math.pi
    east = math.sin(dlon) * math.cos(lat2)
    north = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(east, north)) % 360


def _angle_difference(left: float, right: float) -> float:
    return abs((left - right + 180) % 360 - 180)


def _launch_platforms(situation: dict | None) -> list[tuple[str, tuple[float, float]]]:
    units = situation.get("units", {}) if isinstance(situation, dict) else {}
    if not isinstance(units, dict):
        return []
    candidates = []
    for service in ("army", "navy"):
        for row in units.get(service, []):
            if not isinstance(row, dict) or (point := _point(row)) is None:
                continue
            label = f"{row.get('name', '')} {row.get('sub_type', '')}".lower()
            if any(word in label for word in
                   ("机场", "航空基地", "空军基地", "airport", "airbase", "air base",
                    "航母", "航空母舰", "carrier", "cvn")):
                candidates.append((_guid(row.get("guid")), point))
    return candidates


def _bound_platform_guid(awacs: dict, situation: dict | None) -> str:
    direct = _guid(awacs.get("platform_guid"))
    if direct:
        return direct
    if not isinstance(situation, dict):
        return ""
    missions = awacs.get("mission_guids") or set()
    for row in situation.get("existing_missions", []):
        if not isinstance(row, dict) or _guid(row.get("guid")) not in missions:
            continue
        deployment = row.get("deployment")
        if isinstance(deployment, dict):
            platform = _guid(deployment.get("platform_guid"))
            if platform:
                return platform
    return ""


def _consistent_enemy_axis(situation: dict | None,
                           platform: tuple[float, float]) -> float | None:
    contacts = situation.get("contacts", {}) if isinstance(situation, dict) else {}
    if not isinstance(contacts, dict):
        return None
    directions = []
    seen = set()
    for kind in ("air", "sea", "land", "submarine"):
        for row in contacts.get(kind, []):
            if not isinstance(row, dict) or (point := _point(row)) is None:
                continue
            guid = _guid(row.get("guid") or row.get("id"))
            if guid and guid in seen:
                continue
            if guid:
                seen.add(guid)
            if _distance_km(platform, point) > 1:
                directions.append(_bearing_deg(platform, point))
    # One contact, including one enemy AEW aircraft, is not a confirmed axis.
    if len(directions) < 2:
        return None
    east = sum(math.sin(math.radians(value)) for value in directions)
    north = sum(math.cos(math.radians(value)) for value in directions)
    if math.hypot(east, north) < 1e-6:
        return None
    mean = math.degrees(math.atan2(east, north)) % 360
    if any(_angle_difference(value, mean) > MAX_CONSISTENT_AXIS_DEVIATION_DEG
           for value in directions):
        return None
    return mean


def _rear_status(left: dict, right: dict,
                 situation: dict | None) -> tuple[dict | None, str | None]:
    if {left["type_id"], right["type_id"]} != {CAP_TYPE_ID, AWACS_TYPE_ID}:
        return None, None
    cap = left if left["type_id"] == CAP_TYPE_ID else right
    awacs = left if left["type_id"] == AWACS_TYPE_ID else right
    cap_center, awacs_center = _center(cap["points"]), _center(awacs["points"])
    platforms = _launch_platforms(situation)
    if not platforms:
        return None, f"{cap['name']} 与 {awacs['name']}：缺少可核对的蓝方机场或航母，后方关系仅提示、不硬拒绝"
    bound = _bound_platform_guid(awacs, situation)
    if bound:
        selected = next((point for guid, point in platforms if guid == bound), None)
        if selected is None:
            return None, (f"{awacs['name']} 绑定的起飞平台 {bound} 已不在当前蓝方机场/航母列表；"
                          "后方关系仅提示、不改用另一平台")
        platform = selected
    else:
        _, platform = min(platforms,
                          key=lambda item: (_distance_km(item[1], awacs_center), item[0]))
    axis = _consistent_enemy_axis(situation, platform)
    if axis is not None:
        if (_distance_km(platform, cap_center) < 1
                or _angle_difference(_bearing_deg(platform, cap_center), axis)
                > MAX_SAME_SECTOR_DEVIATION_DEG):
            return None, None  # This CAP covers a different attack sector.
        source = "一致敌情方位"
    else:
        if _distance_km(platform, cap_center) < 1:
            return None, f"{cap['name']} 离起飞平台太近，无法确定预警区的后方关系"
        axis = _bearing_deg(platform, cap_center)
        source = "起飞平台至战斗巡逻区"
    cap_xy, awacs_xy, vertices = _project(([cap_center], [awacs_center], awacs["points"]))
    direction = math.sin(math.radians(axis)), math.cos(math.radians(axis))

    def forward_distance(point) -> float:
        return ((point[0] - cap_xy[0][0]) * direction[0]
                + (point[1] - cap_xy[0][1]) * direction[1])

    rear_offset = -forward_distance(awacs_xy[0])
    behind = sum(forward_distance(point) <= 1e-6 for point in vertices)
    if (rear_offset + 1e-6 >= MIN_AWACS_REAR_OFFSET_KM
            and behind / len(vertices) + 1e-9 >= MIN_AWACS_REAR_VERTEX_FRACTION):
        return None, None
    return ({"kind": "awacs_not_behind_cap", "cap": cap["name"],
             "awacs": awacs["name"], "axis_source": source,
             "rear_offset_km": round(rear_offset, 1),
             "required_rear_offset_km": MIN_AWACS_REAR_OFFSET_KM,
             "vertices_behind": behind, "vertices_total": len(vertices)}, None)


def _error(violation: dict) -> str:
    relation = "区域重叠、包含或接触" if violation["overlap"] else "边界距离过近"
    return (f"巡逻区间距不足：{violation['left']} 与 {violation['right']} "
            f"{relation}；实际边界净距 {violation['actual_km']:g} km，"
            f"至少需要 {violation['required_km']:g} km")


def _rear_error(violation: dict) -> str:
    return (f"预警区位置过前：{violation['awacs']} 相对 {violation['cap']} "
            f"沿{violation['axis_source']}后置 {violation['rear_offset_km']:g} km，"
            f"至少需 {violation['required_rear_offset_km']:g} km；"
            f"后方顶点 {violation['vertices_behind']}/{violation['vertices_total']}，至少需 2/3")


def validate_patrol_spacing(calls: list, existing_areas: list,
                            *, minimums: dict[str, float] | None = None,
                            situation: dict | None = None) -> None:
    """Reject proposed CAP/AWACS areas that overlap or are too close.

    A batch update removes all of its old mission-linked areas from comparison.
    Unrelated old violations remain advisory and never block strike calls.
    Missing active-area geometry fails closed for patrol creation or updates.
    """
    proposed = [call for call in calls if call.skill_id in _SKILL_TYPES]
    if not proposed:
        return
    gaps = _minimums(minimums)
    existing = _existing(existing_areas)
    by_guid = {zone["guid"]: zone for zone in existing if zone["guid"]}
    updated = {_guid(call.existing_mission_guid) for call in proposed
               if _guid(call.existing_mission_guid)}
    for call in proposed:
        own_guid = _guid(call.existing_mission_guid)
        if own_guid and not any(own_guid in zone["mission_guids"] for zone in existing):
            raise ValueError(f"{call.mission_name} 原任务的当前巡逻区域未回读到，不能安全更新")
    candidates = []
    for call in proposed:
        area = call.area if isinstance(call.area, dict) else {}
        reuse_guid = _guid(area.get("existing_area_guid"))
        if reuse_guid:
            reused = by_guid.get(reuse_guid)
            if reused is None or reused["points"] is None:
                raise ValueError(f"{call.mission_name} 复用区域 {reuse_guid} 缺少可核对的几何")
            polygon = reused["points"]
        else:
            polygon = _polygon(area.get("points"))
        if polygon is None:
            raise ValueError(f"{call.mission_name} 新巡逻区缺少有效多边形，不能核对区域间距")
        candidates.append({"guid": reuse_guid, "name": call.mission_name,
                           "type_id": _SKILL_TYPES[call.skill_id],
                           "points": polygon,
                           "platform_guid": _guid((area.get("deployment") or {}).get("platform_guid"))
                           if isinstance(area.get("deployment"), dict) else "",
                           "mission_guids": {_guid(call.existing_mission_guid)}
                           if _guid(call.existing_mission_guid) else set()})
    active = []
    for zone in existing:
        if zone["mission_guids"] and zone["mission_guids"] <= updated:
            continue
        if zone["points"] is None:
            raise ValueError(f"当前巡逻区 {zone['name']} 缺少几何，不能安全新增或更新巡逻区")
        active.append(zone)
    for candidate in candidates:
        for zone in active:
            violation = _violation(candidate, zone, gaps)
            if violation:
                raise ValueError(_error(violation))
            rear, _ = _rear_status(candidate, zone, situation)
            if rear:
                raise ValueError(_rear_error(rear))
    for index, candidate in enumerate(candidates):
        for other in candidates[index + 1:]:
            violation = _violation(candidate, other, gaps)
            if violation:
                raise ValueError(_error(violation))
            rear, _ = _rear_status(candidate, other, situation)
            if rear:
                raise ValueError(_rear_error(rear))


def patrol_spacing_advisory(existing_areas: list,
                            *, situation: dict | None = None) -> dict:
    """Compact active-area geometry and pre-existing conflicts for Agent input."""
    zones = _existing(existing_areas)
    areas = []
    for zone in zones:
        summary = {"guid": zone["guid"], "name": zone["name"],
                   "type_id": zone["type_id"],
                   "mission_guids": sorted(zone["mission_guids"])}
        points = zone["points"]
        if points is None:
            summary["geometry"] = "missing"
        else:
            latitudes = [point[0] for point in points]
            longitudes = [point[1] for point in points]
            summary["center"] = {"lat": round(sum(latitudes) / len(points), 5),
                                 "lon": round(sum(longitudes) / len(points), 5)}
            summary["bounds"] = {"min_lat": round(min(latitudes), 5),
                                 "max_lat": round(max(latitudes), 5),
                                 "min_lon": round(min(longitudes), 5),
                                 "max_lon": round(max(longitudes), 5)}
        areas.append(summary)
    conflicts = []
    warnings = []
    for index, left in enumerate(zones):
        if left["points"] is None:
            continue
        for right in zones[index + 1:]:
            if right["points"] is None:
                continue
            violation = _violation(left, right, DEFAULT_MIN_GAPS_KM)
            if violation:
                conflicts.append(violation)
            rear, warning = _rear_status(left, right, situation)
            if rear:
                conflicts.append(rear)
            if warning:
                warnings.append(warning)
    return {"minimum_gaps_km": dict(DEFAULT_MIN_GAPS_KM),
            "awacs_rear_offset_km": MIN_AWACS_REAR_OFFSET_KM,
            "awacs_rear_vertex_fraction": round(MIN_AWACS_REAR_VERTEX_FRACTION, 3),
            "areas": areas, "conflicts": conflicts, "warnings": warnings}
