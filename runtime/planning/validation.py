"""LLM 方案的确定性规则校验；模型不能绕过这些规则。"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any

from runtime.contracts import SERVICES, SERVICE_UNIT_TYPES, SkillCall
from runtime.planning.plan_adapter import normalize_plan
from runtime.skills.registry import SkillRegistry
from rules.awacs_area import make_awacs_area
from rules.awacs_deployment import validate_deployments
from rules.awacs_triggers import prepare_area, validate_call
from rules.jammer_support import is_jammer
from rules.air_weapon_roles import compatible_with_skill, role_for_skill
from rules.patrol_spacing import validate_patrol_spacing
from rules.sea_strike_allocation import validate_groups


_GUID = re.compile(r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$")


def _guid(value: str, label: str) -> str:
    if not isinstance(value, str) or not _GUID.fullmatch(value.strip()):
        raise ValueError(f"{label} 必须是有效 GUID")
    return value.strip().strip("{}").lower()


def _points(area: dict[str, Any], label: str) -> None:
    existing = str(area.get("existing_area_guid", "")).strip()
    if existing:
        _guid(existing, f"{label}.existing_area_guid")
        return
    points = area.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError(f"{label}.points 至少需要 3 个点")
    for index, point in enumerate(points, 1):
        if not isinstance(point, dict):
            raise ValueError(f"{label}.points[{index}] 必须是对象")
        lat, lon = point.get("lat"), point.get("lon")
        if (isinstance(lat, bool) or isinstance(lon, bool)
                or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))
                or not math.isfinite(lat) or not math.isfinite(lon)
                or not -90 <= lat <= 90 or not -180 <= lon <= 180):
            raise ValueError(f"{label}.points[{index}] 经纬度无效")


def _route(route: dict[str, Any], label: str) -> None:
    if not isinstance(route, dict):
        raise ValueError(f"{label} 必须是航线对象，包含 points 坐标点数组")
    existing = str(route.get("existing_route_guid", "")).strip()
    if existing:
        _guid(existing, f"{label}.existing_route_guid")
        return
    points = route.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError(f"{label}.points 至少需要 2 个点")
    for index, point in enumerate(points, 1):
        _points({"points": [point, point, point]}, f"{label}.points[{index}]")


def _skill_parameters(values: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    unknown = sorted(set(values) - set(schema))
    if unknown:
        raise ValueError(f"{label} 包含 Skill 未声明参数：{', '.join(unknown)}")
    for name, contract in schema.items():
        if not isinstance(contract, dict):
            raise ValueError(f"Skill 参数合同 {name} 必须是对象")
        if contract.get("required") and name not in values:
            raise ValueError(f"{label}.{name} 是必填参数")
        if name not in values:
            continue
        value = values[name]
        kind = contract.get("type")
        valid = {
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }.get(kind, False)
        if not valid:
            raise ValueError(f"{label}.{name} 类型必须为 {kind}")
        if "enum" in contract and value not in contract["enum"]:
            raise ValueError(f"{label}.{name} 不在允许枚举中")
        if kind in {"number", "integer"}:
            if not math.isfinite(value):
                raise ValueError(f"{label}.{name} 必须是有限数值")
            if "minimum" in contract and value < contract["minimum"]:
                raise ValueError(f"{label}.{name} 小于最小值")
            if "maximum" in contract and value > contract["maximum"]:
                raise ValueError(f"{label}.{name} 大于最大值")


def normalize_situation(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("situation 必须是对象")
    side = raw.get("side")
    scenario = raw.get("scenario")
    units = raw.get("units")
    contacts = raw.get("contacts")
    if not isinstance(side, dict) or not isinstance(scenario, dict):
        raise ValueError("situation 缺少 side 或 scenario")
    _guid(str(side.get("guid", "")), "side.guid")
    if not str(side.get("name", "")).strip():
        raise ValueError("side.name 不能为空")
    for key in ("start_time", "end_time"):
        if not isinstance(scenario.get(key), str) or not scenario[key].strip():
            raise ValueError(f"scenario.{key} 不能为空")
    if not isinstance(units, dict) or not isinstance(contacts, dict):
        raise ValueError("situation 缺少 units 或 contacts")
    for service in SERVICES:
        if not isinstance(units.get(service, []), list):
            raise ValueError(f"units.{service} 必须是数组")
    for kind in ("air", "sea", "land", "submarine"):
        if not isinstance(contacts.get(kind, []), list):
            raise ValueError(f"contacts.{kind} 必须是数组")
    if "mission_assignments" in raw and not isinstance(raw["mission_assignments"], dict):
        raise ValueError("mission_assignments 必须是飞机 GUID 到任务 GUID 的对象")
    return raw


def validate_aircraft_availability(calls: list[SkillCall], situation: dict[str, Any]) -> None:
    """An aircraft can only be reused while updating the mission that owns it."""
    assignments = {
        _guid(str(unit), "mission_assignments.飞机GUID"):
        _guid(str(mission), "mission_assignments.任务GUID")
        for unit, mission in (situation.get("mission_assignments") or {}).items()
    }
    for call in calls:
        own_mission = (str(call.existing_mission_guid).strip("{}").lower()
                       if call.existing_mission_guid else "")
        for raw_guid in (*call.unit_guids, *call.escort_unit_guids):
            unit_guid = _guid(raw_guid, f"{call.call_id}.飞机GUID")
            occupied_mission = assignments.get(unit_guid)
            if occupied_mission and occupied_mission != own_mission:
                raise ValueError(
                    f"飞机已被任务占用：{unit_guid} 属于任务 {occupied_mission}；"
                    f"{call.mission_name} 不能再次选择")


def validate_plan(plan: dict[str, Any], situation: dict[str, Any], registry: SkillRegistry) -> list[SkillCall]:
    situation = normalize_situation(situation)
    plan = normalize_plan(plan)
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ValueError("任务方案必须是 version=1 的对象")
    calls_raw = plan.get("skill_calls")
    if not isinstance(calls_raw, list) or len(calls_raw) > 100:
        raise ValueError("skill_calls 必须是最多 100 项的数组")

    unit_index: dict[str, tuple[str, dict]] = {}
    for service, rows in situation["units"].items():
        if service not in SERVICES:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            guid = _guid(str(row.get("guid", row.get("id", ""))), f"units.{service}.guid")
            if guid in unit_index:
                raise ValueError(f"态势中单位 GUID 重复：{guid}")
            unit_index[guid] = (service, row)

    contact_index: dict[str, tuple[str, dict]] = {}
    contact_to_kind = {"air": "Aircraft", "sea": "Ship", "land": "Facility", "submarine": "Submarine"}
    for group, rows in situation["contacts"].items():
        if group not in contact_to_kind:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            guid = _guid(str(row.get("guid", row.get("id", ""))), f"contacts.{group}.guid")
            contact_index[guid] = (contact_to_kind[group], row)

    calls: list[SkillCall] = []
    seen_calls: set[str] = set()
    seen_names: set[str] = set()
    assigned_units: dict[str, tuple[str, str]] = {}
    for index, raw in enumerate(calls_raw, 1):
        call = SkillCall.from_dict(raw)
        if (not re.fullmatch(r"[0-9A-Za-z._-]{1,100}", call.call_id)
                or call.call_id in seen_calls):
            raise ValueError(f"skill_calls[{index}].call_id 为空或重复")
        if (not call.mission_name or len(call.mission_name) > 200
                or call.mission_name in seen_names):
            raise ValueError(f"skill_calls[{index}].mission_name 为空或重复")
        if len(call.reason) > 1000:
            raise ValueError(f"{call.call_id}.reason 不能超过1000字符")
        spec = registry.get(call.skill_id)
        if not call.unit_guids or len(call.unit_guids) > 100:
            raise ValueError(f"{call.call_id} 至少需要一个主任务单元")
        if len({str(item) for item in call.unit_guids}) != len(call.unit_guids):
            raise ValueError(f"{call.call_id}.unit_guids 内部重复")
        if len({str(item) for item in call.escort_unit_guids}) != len(call.escort_unit_guids):
            raise ValueError(f"{call.call_id}.escort_unit_guids 内部重复")
        if len({str(item) for item in call.target_contact_guids}) != len(call.target_contact_guids):
            raise ValueError(f"{call.call_id}.target_contact_guids 内部重复")
        normalized_units = []
        for raw_guid in call.unit_guids:
            guid = _guid(raw_guid, f"{call.call_id}.unit_guids")
            if guid in assigned_units:
                previous_call, previous_name = assigned_units[guid]
                unit_name = unit_index.get(guid, ("", {}))[1].get("name", "")
                raise ValueError(
                    f"同一批方案中单位重复分配：{guid}（{unit_name}）；"
                    f"{previous_name} [{previous_call}] 与 {call.mission_name} [{call.call_id}]")
            if guid not in unit_index or unit_index[guid][0] != spec.service:
                raise ValueError(f"{call.call_id} 使用了不属于 {spec.service} 的单位：{guid}")
            unit_type = str(unit_index[guid][1].get("unit_type", unit_index[guid][1].get("UnitType", "")))
            if unit_type not in SERVICE_UNIT_TYPES[spec.service]:
                raise ValueError(f"{call.call_id} 单元类型 {unit_type or '未知'} 不属于 {spec.service} 军种")
            if unit_type and unit_type not in spec.unit_types:
                raise ValueError(f"{call.call_id} 单元类型 {unit_type} 不适用 {spec.skill_id}")
            if spec.service == "air" and is_jammer(unit_index[guid][1]):
                raise ValueError(f"{call.call_id} 干扰机只能由独立电子干扰规则控制，不能作为 {spec.skill_id} 主任务单元")
            if role_for_skill(spec.skill_id) and not call.loadout_guid and not compatible_with_skill(
                    unit_index[guid][1], spec.skill_id,
                    live=bool(situation.get("weapon_inventory_live"))):
                raise ValueError(
                    f"{call.call_id} 飞机 {unit_index[guid][1].get('name', guid)} "
                    f"当前挂载无可用{spec.target_kind}打击武器或挂载信息未知，不能执行 {spec.skill_id}")
            normalized_units.append(guid)
        assigned_units.update({guid: (call.call_id, call.mission_name) for guid in normalized_units})

        if call.escort_unit_guids and not spec.supports_escort:
            raise ValueError(f"{spec.skill_id} 不支持护航单元")
        for raw_guid in call.escort_unit_guids:
            guid = _guid(raw_guid, f"{call.call_id}.escort_unit_guids")
            if guid not in unit_index or unit_index[guid][0] != "air":
                raise ValueError(f"护航单元必须是蓝方空军单位：{guid}")
            if is_jammer(unit_index[guid][1]):
                raise ValueError(f"{call.call_id} 干扰机只能由独立电子干扰规则控制，不能通过护航字段分配")
            if guid in normalized_units:
                raise ValueError(f"主攻单元不能同时作为护航单元：{guid}")
            if guid in assigned_units:
                previous_call, previous_name = assigned_units[guid]
                raise ValueError(
                    f"同一批方案中单位重复分配：{guid}；"
                    f"{previous_name} [{previous_call}] 与 {call.mission_name} [{call.call_id}]（护航）")
            assigned_units[guid] = (call.call_id, call.mission_name)

        if call.loadout_guid and not spec.supports_loadout:
            raise ValueError(f"{spec.skill_id} 不支持换挂")
        if call.loadout_guid and (len(call.loadout_guid) > 160 or
                                  not re.fullmatch(r"[0-9A-Za-z._:-]+", call.loadout_guid)):
            raise ValueError(f"{call.call_id}.loadout_guid 格式无效")
        if call.existing_mission_guid:
            _guid(call.existing_mission_guid, f"{call.call_id}.existing_mission_guid")

        if spec.requires_target:
            if not call.target_contact_guids:
                raise ValueError(f"{call.call_id} 是打击任务，必须选择蓝方当前接触")
            for raw_guid in call.target_contact_guids:
                guid = _guid(raw_guid, f"{call.call_id}.target_contact_guids")
                if guid not in contact_index:
                    raise ValueError(f"{call.call_id} 目标不是蓝方当前接触：{guid}")
                if contact_index[guid][0] != spec.target_kind:
                    raise ValueError(f"{call.call_id} 目标类型不匹配，要求 {spec.target_kind}")
        elif call.target_contact_guids:
            raise ValueError(f"{call.call_id} 不是打击任务，不能填写目标")

        if call.skill_id == "air.early_warning":
            if len(normalized_units) != 1:
                raise ValueError(f"{call.call_id} 空中预警每项只分配一架预警机，以便锚定其机场")
            trigger = situation.get("operational_rules", {}).get("awacs_trigger")
            reused = prepare_area(call, trigger) if trigger else None
            call = replace(call, area=reused or make_awacs_area(
                call, situation, unit_index[normalized_units[0]][1]))
            if trigger:
                validate_call(call, trigger)

        if spec.requires_area:
            if not isinstance(call.area, dict):
                raise ValueError(f"{call.call_id} 必须划定区域")
            _points(call.area, f"{call.call_id}.area")
        elif call.area is not None:
            raise ValueError(f"{call.call_id} 不使用任务区域")

        if call.routes is not None:
            if not spec.supports_routes:
                raise ValueError(f"{call.call_id} 不支持航线")
            if not isinstance(call.routes, dict):
                raise ValueError(f"{call.call_id}.routes 必须是对象")
            unknown_routes = set(call.routes) - {"outbound", "on_station"}
            if unknown_routes:
                raise ValueError(f"{call.call_id}.routes 含未知航线：{', '.join(sorted(unknown_routes))}")
            for route_name in ("outbound", "on_station"):
                if call.routes.get(route_name) is not None:
                    _route(call.routes[route_name], f"{call.call_id}.routes.{route_name}")

        _skill_parameters(call.parameters, spec.parameter_schema,
                          f"{call.call_id}.parameters")

        seen_calls.add(call.call_id)
        seen_names.add(call.mission_name)
        calls.append(call)
    validate_deployments(calls, situation)
    # Compare the completed batch with live, mission-linked Mozi airspace.
    # Updated missions replace their own old polygon in this comparison.
    validate_patrol_spacing(calls, situation.get("existing_areas", []),
                            situation=situation)
    validate_aircraft_availability(calls, situation)
    validate_groups(calls, situation)
    return calls
