"""Generate a separate warning polygon around an existing patrol polygon.

The patrol polygon itself is unchanged. A 32-sided circumscribed disk around
its convex hull covers every point within 100 km of that hull. Concave patrol
polygons therefore receive a conservative (possibly larger) warning area.
Coordinates use a local azimuthal equidistant projection, then return to WGS84.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088
ALERT_BUFFER_KM = 100.0
ALERT_AREA_TYPE_ID = "106"
_CIRCLE_SIDES = 32


def _center(points: list[dict]) -> tuple[float, float]:
    x = y = z = 0.0
    for point in points:
        lat = math.radians(float(point["lat"]))
        lon = math.radians(float(point["lon"]))
        x += math.cos(lat) * math.cos(lon)
        y += math.cos(lat) * math.sin(lon)
        z += math.sin(lat)
    if math.hypot(x, y) + abs(z) < 1e-9:
        raise ValueError("巡逻区跨度过大，无法计算警戒区中心")
    return math.atan2(z, math.hypot(x, y)), math.atan2(y, x)


def _project(point: dict, lat0: float, lon0: float) -> tuple[float, float]:
    lat = math.radians(float(point["lat"]))
    lon = math.radians(float(point["lon"]))
    delta = lon - lon0
    cosine = max(-1.0, min(1.0, math.sin(lat0) * math.sin(lat) +
                                 math.cos(lat0) * math.cos(lat) * math.cos(delta)))
    arc = math.acos(cosine)
    if arc < 1e-10:
        return (0.0, 0.0)
    sine = math.sin(arc)
    if abs(sine) < 1e-10:
        raise ValueError("巡逻区参考点距中心过远，不能可靠外扩")
    k = arc / sine
    return (EARTH_RADIUS_KM * k * math.cos(lat) * math.sin(delta),
            EARTH_RADIUS_KM * k * (math.cos(lat0) * math.sin(lat) -
                                   math.sin(lat0) * math.cos(lat) * math.cos(delta)))


def _unproject(x: float, y: float, lat0: float, lon0: float) -> dict[str, float]:
    distance = math.hypot(x, y)
    if distance < 1e-10:
        lat, lon = lat0, lon0
    else:
        arc = distance / EARTH_RADIUS_KM
        lat = math.asin(max(-1.0, min(1.0,
            math.cos(arc) * math.sin(lat0) +
            y * math.sin(arc) * math.cos(lat0) / distance)))
        lon = lon0 + math.atan2(
            x * math.sin(arc),
            distance * math.cos(lat0) * math.cos(arc) -
            y * math.sin(lat0) * math.sin(arc))
    longitude = (math.degrees(lon) + 180.0) % 360.0 - 180.0
    return {"lat": round(math.degrees(lat), 8), "lon": round(longitude, 8)}


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) < 3:
        raise ValueError("巡逻区须有至少三个不同参考点")
    def cross(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1]) -
                (b[1] - a[1]) * (c[0] - a[0]))
    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def buffer_patrol_points(points: list[dict], distance_km: float = ALERT_BUFFER_KM) -> list[dict]:
    """Return a conservative outward buffer; never reuse patrol reference points."""
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("巡逻区至少需要三个参考点才能生成警戒区")
    if isinstance(distance_km, bool) or not isinstance(distance_km, (int, float)):
        raise ValueError("警戒区外扩距离必须为公里数")
    if not math.isfinite(distance_km) or distance_km <= 0:
        raise ValueError("警戒区外扩距离必须大于零")
    clean = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("巡逻区参考点必须为对象")
        lat, lon = point.get("lat"), point.get("lon")
        if (type(lat) not in (int, float) or type(lon) not in (int, float) or
                not math.isfinite(lat) or not math.isfinite(lon) or
                not -90 <= lat <= 90 or not -180 <= lon <= 180):
            raise ValueError("巡逻区参考点经纬度无效")
        clean.append({"lat": float(lat), "lon": float(lon)})
    if clean[0] == clean[-1]:
        clean.pop()
    lat0, lon0 = _center(clean)
    projected = [_project(point, lat0, lon0) for point in clean]
    source_hull = _hull(projected)
    twice_area = abs(sum(source_hull[i][0] * source_hull[(i + 1) % len(source_hull)][1] -
                         source_hull[(i + 1) % len(source_hull)][0] * source_hull[i][1]
                         for i in range(len(source_hull))))
    if twice_area < 1e-6:
        raise ValueError("巡逻区参考点几乎共线，无法外扩")
    radius = float(distance_km) / math.cos(math.pi / _CIRCLE_SIDES)
    cloud = [(x + radius * math.cos(2 * math.pi * k / _CIRCLE_SIDES),
              y + radius * math.sin(2 * math.pi * k / _CIRCLE_SIDES))
             for x, y in source_hull for k in range(_CIRCLE_SIDES)]
    return [_unproject(x, y, lat0, lon0) for x, y in _hull(cloud)]


def patrol_alert_area(patrol_area: dict, source_points: list[dict]) -> dict:
    """Compile a distinct type 106 warning area from a patrol area's geometry."""
    if not isinstance(patrol_area, dict):
        raise ValueError("巡逻区配置无效")
    name = str(patrol_area.get("name") or "巡逻区").strip()
    return {"existingAreaGuid": "", "typeId": ALERT_AREA_TYPE_ID,
            "name": f"{name}-警戒区",
            "remark": f"{name}外扩100公里警戒区",
            "points": buffer_patrol_points(source_points)}
