"""Generate an AWACS racetrack from an Agent's deployment parameters."""

from __future__ import annotations

import math


EARTH_RADIUS_KM = 6371.0088
STRAIGHT_LENGTH_KM = 30.0  # L: 60 km overall length minus the 30 km diameter
TURN_RADIUS_KM = 15.0      # r: half of the 30 km overall width
PATROL_LIMIT_KM = 300.0
# The continuous stadium reaches sqrt(d**2 + (L/2)**2) + r from its origin.
# 284 km leaves a small margin below the mathematical ~284.6 km limit.
MAX_FORWARD_KM = 284.0


def _position(row: dict) -> tuple[float, float] | None:
    try:
        if type(row.get("lat")) is bool or type(row.get("lon")) is bool:
            return None
        lat, lon = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 <= lat <= 85 and -180 <= lon <= 180):
        return None
    return lat, lon


def is_awacs(aircraft: dict) -> bool:
    label = f"{aircraft.get('name', '')} {aircraft.get('sub_type', '')}".upper()
    return any(word in label for word in ("预警", "AEW", "AWACS", "E-2", "E-3", "E-7", "A-50", "KJ-"))


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat = lat2 - lat1
    dlon = (lon2 - lon1 + math.pi) % (2 * math.pi) - math.pi
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(hav)))


def _destination(origin: tuple[float, float], bearing_deg: float, distance_km: float) -> tuple[float, float]:
    lat1, lon1 = map(math.radians, origin)
    bearing = math.radians(bearing_deg)
    angle = distance_km / EARTH_RADIUS_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(angle)
                     + math.cos(lat1) * math.sin(angle) * math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing) * math.sin(angle) * math.cos(lat1),
                             math.cos(angle) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), (math.degrees(lon2) + 180) % 360 - 180


def _racetrack(center: tuple[float, float], long_axis_deg: float) -> list[dict[str, float]]:
    """Fourteen ordered boundary points of a stadium: total 60 x 30 km."""
    axis = math.radians(long_axis_deg)
    half_straight = STRAIGHT_LENGTH_KM / 2
    points = []
    for end, start in ((half_straight, -90), (-half_straight, 90)):
        for step in range(7):
            angle = math.radians(start + step * 30)
            along = end + TURN_RADIUS_KM * math.cos(angle)
            across = TURN_RADIUS_KM * math.sin(angle)
            east = along * math.sin(axis) + across * math.cos(axis)
            north = along * math.cos(axis) - across * math.sin(axis)
            lat, lon = _destination(center, math.degrees(math.atan2(east, north)),
                                    math.hypot(east, north))
            points.append({"lat": round(lat, 7), "lon": round(lon, 7)})
    return points


def _platforms(situation: dict) -> list[tuple[str, str, tuple[float, float]]]:
    candidates = []
    for row in situation["units"].get("army", []):
        text = f"{row.get('name', '')} {row.get('sub_type', '')}".lower()
        point = _position(row)
        if point and any(word in text for word in ("机场", "航空基地", "空军基地", "airport", "airbase", "air base")):
            candidates.append((str(row.get("guid", "")), str(row.get("name", "")), point))
    for row in situation["units"].get("navy", []):
        text = f"{row.get('name', '')} {row.get('sub_type', '')}".lower()
        point = _position(row)
        if point and any(word in text for word in ("航母", "航空母舰", "carrier", "cvn")):
            candidates.append((str(row.get("guid", "")), str(row.get("name", "")), point))
    return candidates


def _deployment(call, situation: dict) -> tuple[float, float]:
    area = call.area
    if not isinstance(area, dict):
        raise ValueError(f"{call.call_id} 空中预警 area 必须包含跑马道部署参数")
    unknown = set(area) - {"pattern", "attack_bearing_deg", "forward_km"}
    if unknown:
        raise ValueError(f"{call.call_id} 空中预警 area 不能直接填写区域点或复用旧区域：{', '.join(sorted(unknown))}")
    if area.get("pattern") != "racetrack":
        raise ValueError(f"{call.call_id} 空中预警 area.pattern 必须为 racetrack")
    has_axis = "attack_bearing_deg" in area
    has_forward = "forward_km" in area
    if has_axis != has_forward:
        raise ValueError(f"{call.call_id} 空中预警进攻轴向与前推距离必须同时填写")
    if not has_axis:
        return 0.0, 0.0  # Unknown attack axis: east-west long axis, no advance.
    bearing, forward = area["attack_bearing_deg"], area["forward_km"]
    if (type(bearing) not in (int, float) or not math.isfinite(bearing)
            or not 0 <= bearing < 360):
        raise ValueError(f"{call.call_id} area.attack_bearing_deg 必须在 0 到 360 度之间")
    retreat = situation.get("operational_rules", {}).get("awacs_trigger", {}).get("kind") == "threat_retreat"
    minimum = -MAX_FORWARD_KM if retreat else 0
    if (type(forward) not in (int, float) or not math.isfinite(forward)
            or not minimum <= forward <= MAX_FORWARD_KM):
        raise ValueError(f"{call.call_id} area.forward_km 必须在 {minimum:g} 到 {MAX_FORWARD_KM:g} km 之间")
    return float(bearing), float(forward)


def make_awacs_area(call, situation: dict, aircraft: dict) -> dict:
    """Use nearest blue airport/carrier, then expand the Agent's axis and distance."""
    if not is_awacs(aircraft):
        raise ValueError(f"{call.call_id} 空中预警只能分配预警机：{aircraft.get('name', '')}")
    aircraft_position = _position(aircraft)
    if aircraft_position is None:
        raise ValueError(f"{call.call_id} 预警机缺少当前位置，无法选最近起飞平台")
    candidates = _platforms(situation)
    if not candidates:
        raise ValueError(f"{call.call_id} 态势中没有蓝方机场或航母，无法创建预警区")
    original = situation.get("operational_rules", {}).get("awacs_trigger", {}).get("deployment", {}).get("platform_guid")
    if call.existing_mission_guid and original:
        selected = next((item for item in candidates if item[0].strip("{}").lower() == str(original).strip("{}").lower()), None)
        if selected is None:
            raise ValueError(f"{call.call_id} 原预警起飞平台已不可用，不能偷偷切换平台")
        origin_guid, origin_name, origin = selected
    else:
        origin_guid, origin_name, origin = min(
            candidates, key=lambda item: (_distance_km(aircraft_position, item[2]), item[0]))
    bearing, forward_km = _deployment(call, situation)
    if "attack_bearing_deg" in call.area:
        long_axis = (bearing + 90) % 360
        axis_note = f"进攻轴向{bearing:g}°;长轴{long_axis:g}°"
    else:
        long_axis = 90.0
        axis_note = "无已知进攻轴向;长轴暂取东西向90°"
    center = _destination(origin, bearing, forward_km)
    points = _racetrack(center, long_axis)
    distances = [_distance_km(origin, (point["lat"], point["lon"])) for point in points]
    if max(distances) > PATROL_LIMIT_KM + 1e-6:
        raise ValueError(f"{call.call_id} 预警跑马道超出起飞平台 {PATROL_LIMIT_KM:g} km 范围")
    return {
        "name": f"{call.mission_name}-预警跑马道",
        "remark": (f"规则生成;最近起飞平台:{origin_name}({origin_guid});前推{forward_km:g}km;"
                   f"{axis_note};L={STRAIGHT_LENGTH_KM:g}km;r={TURN_RADIUS_KM:g}km;"
                   f"最远{max(distances):.1f}km"),
        "points": points,
        "deployment": {"mode": "threat" if "attack_bearing_deg" in call.area else "local",
                       "bearing_deg": bearing, "forward_km": forward_km,
                       "platform_guid": origin_guid},
    }
