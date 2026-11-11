"""Read blue-side aircraft mission ownership without modifying the scenario.

Unknown MissionGant shapes fail closed: an aircraft must not be considered free
merely because the task-list response could not be interpreted.
"""

from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import urlopen
from uuid import UUID


_MISSION_ID_FIELDS = ("missionGuid", "MissionGuid", "guid", "Guid", "id", "ID", "key")
_UNIT_ID_FIELDS = ("id", "ID", "key", "guid", "Guid")


def _guid(value: object, label: str) -> str:
    try:
        return str(UUID(str(value).strip().strip("{}")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"任务分配数据的 {label} 不是有效 GUID：{value!r}") from exc


def _first_guid(row: dict, keys: tuple[str, ...], label: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return _guid(value, label)
    raise RuntimeError(f"任务分配数据缺少 {label}")


def _unit_ids(assigned: object, mission_guid: str) -> list[str]:
    if assigned is None:
        return []
    if not isinstance(assigned, list):
        raise RuntimeError(f"任务 {mission_guid} 的 assignUnit 不是列表")
    result: list[str] = []
    for item in assigned:
        if not isinstance(item, dict):
            raise RuntimeError(f"任务 {mission_guid} 的 assignUnit 含非对象条目")
        if "children" not in item:
            result.append(_first_guid(item, _UNIT_ID_FIELDS, "单位 GUID"))
            continue
        children = item["children"]
        if children in (None, ""):
            # A singleton may be represented with children="" and key=<unit>.
            result.append(_first_guid(item, _UNIT_ID_FIELDS, "单位 GUID"))
            continue
        if not isinstance(children, list):
            raise RuntimeError(f"任务 {mission_guid} 的 assignUnit.children 格式未知")
        if not children:
            result.append(_first_guid(item, _UNIT_ID_FIELDS, "单位或分组 GUID"))
            continue
        for child in children:
            if not isinstance(child, dict):
                raise RuntimeError(f"任务 {mission_guid} 的 assignUnit.children 含非对象条目")
            result.append(_first_guid(child, _UNIT_ID_FIELDS, "单位 GUID"))
    return result


def _detail_unit_ids(base: str, mission_guid: str) -> list[str]:
    """Read assigned units from Mission detail when MissionGant omits them.

    Missing or malformed detail remains an error: otherwise an assigned
    aircraft could be incorrectly treated as available for a new task.
    """
    try:
        with urlopen(f"{base}/Mission?Guid={quote(mission_guid)}", timeout=12) as response:
            payload = json.load(response)
    except Exception as exc:
        raise RuntimeError(
            f"任务 {mission_guid} 缺少 assignUnit，任务详情读取失败；不能判定飞机空闲"
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) and payload.get("code") == 0 else None
    basic = data.get("missionBasicData") if isinstance(data, dict) else None
    if not isinstance(basic, dict) or "assignedCombatUnit" not in basic:
        raise RuntimeError(
            f"任务 {mission_guid} 缺少 assignUnit，详情也缺少 assignedCombatUnit；不能判定飞机空闲"
        )
    assigned = basic["assignedCombatUnit"]
    if not isinstance(assigned, list):
        raise RuntimeError(
            f"任务 {mission_guid} 的 assignedCombatUnit 格式未知；不能判定飞机空闲"
        )
    return _unit_ids(assigned, mission_guid)


def read_blue_mission_assignments(
    api_base: str, side_guid: str, aircraft_guids: set[str]
) -> dict[str, str]:
    """Return ``{aircraft_guid: mission_guid}`` for assigned blue aircraft.

    ``MissionGant`` must return ``code=0`` and either ``data=null`` (no missions)
    or a list of mission rows. Each row must identify its mission. When a row
    omits ``assignUnit``, its ``Mission`` detail must expose
    ``missionBasicData.assignedCombatUnit``. Flat and grouped units are accepted.
    """
    aircraft = {_guid(value, "飞机 GUID") for value in aircraft_guids}
    if not aircraft:
        return {}
    side = _guid(side_guid, "蓝方 GUID")
    base = str(api_base).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("api_base 必须是 HTTP(S) 地址")
    url = f"{base}/MissionGant?SideGuid={quote(side)}"
    try:
        with urlopen(url, timeout=12) as response:
            payload = json.load(response)
    except Exception as exc:
        raise RuntimeError("读取蓝方任务分配失败；不能判定飞机空闲") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError("墨子未成功返回蓝方任务分配；不能判定飞机空闲")
    if "data" not in payload:
        raise RuntimeError("蓝方任务分配缺少 data；不能判定飞机空闲")
    rows = payload.get("data")
    if rows is None:
        return {}
    if not isinstance(rows, list):
        raise RuntimeError("蓝方任务分配 data 格式未知；不能判定飞机空闲")
    occupied: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("蓝方任务分配包含非对象任务")
        mission_guid = _first_guid(row, _MISSION_ID_FIELDS, "任务 GUID")
        unit_guids = (_unit_ids(row["assignUnit"], mission_guid)
                      if "assignUnit" in row else _detail_unit_ids(base, mission_guid))
        for unit_guid in unit_guids:
            if unit_guid not in aircraft:
                continue
            previous = occupied.get(unit_guid)
            if previous is not None and previous != mission_guid:
                raise RuntimeError(f"飞机 {unit_guid} 同时出现在任务 {previous} 和 {mission_guid}")
            occupied[unit_guid] = mission_guid
    return occupied
