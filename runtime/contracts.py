"""脚本 Skill 架构的稳定数据协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SERVICES = ("air", "navy", "army")
AGENT_OWNERS = ("air", "awacs", "navy", "army")
SERVICE_UNIT_TYPES = {
    "air": frozenset({"Aircraft"}),
    "navy": frozenset({"Ship"}),
    "army": frozenset({"Facility"}),
}
TARGET_KINDS = ("Aircraft", "Ship", "Facility", "Submarine")


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    service: str
    display_name: str
    description: str
    task_key: str
    script: str
    mission_type: str
    classify: str
    unit_types: tuple[str, ...]
    requires_area: bool
    area_type_id: str | None
    requires_target: bool
    target_kind: str | None
    supports_escort: bool = False
    supports_loadout: bool = False
    supports_routes: bool = False
    agent_owner: str = ""
    rules: tuple[str, ...] = ()
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path) -> "SkillSpec":
        required = {
            "schema_version", "skill_id", "service", "display_name", "description",
            "task_key", "script", "mission_type", "classify", "unit_types",
            "requires_area", "requires_target",
        }
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f"{path} 缺少字段：{', '.join(missing)}")
        if data["schema_version"] != 1:
            raise ValueError(f"{path} schema_version 必须为 1")
        if data["service"] not in SERVICES:
            raise ValueError(f"{path} service 无效")
        agent_owner = data.get("agent_owner", data["service"])
        if agent_owner not in AGENT_OWNERS or (agent_owner != data["service"]
                                                and not (agent_owner == "awacs" and data["service"] == "air")):
            raise ValueError(f"{path} agent_owner 与 service 不匹配")
        if not isinstance(data["mission_type"], str) or not data["mission_type"].strip():
            raise ValueError(f"{path} mission_type 必须是非空字符串")
        if not isinstance(data["unit_types"], list) or not data["unit_types"]:
            raise ValueError(f"{path} unit_types 必须为非空数组")
        target_kind = data.get("target_kind")
        if target_kind is not None and target_kind not in TARGET_KINDS:
            raise ValueError(f"{path} target_kind 无效")
        if data["requires_area"] and not str(data.get("area_type_id", "")).strip():
            raise ValueError(f"{path} 需要区域时必须声明 area_type_id")
        if data["requires_target"] and target_kind is None:
            raise ValueError(f"{path} 需要目标时必须声明 target_kind")
        return cls(
            skill_id=str(data["skill_id"]),
            service=str(data["service"]),
            display_name=str(data["display_name"]),
            description=str(data["description"]),
            task_key=str(data["task_key"]),
            script=str(data["script"]),
            mission_type=str(data["mission_type"]),
            classify=str(data["classify"]),
            unit_types=tuple(str(item) for item in data["unit_types"]),
            requires_area=bool(data["requires_area"]),
            area_type_id=(str(data["area_type_id"]) if data.get("area_type_id") else None),
            requires_target=bool(data["requires_target"]),
            target_kind=target_kind,
            supports_escort=bool(data.get("supports_escort", False)),
            supports_loadout=bool(data.get("supports_loadout", False)),
            supports_routes=bool(data.get("supports_routes", False)),
            agent_owner=str(agent_owner),
            rules=tuple(str(item) for item in data.get("rules", [])),
            parameter_schema=(data.get("parameter_schema", {})
                              if isinstance(data.get("parameter_schema", {}), dict) else {}),
            path=path,
        )

    def public_contract(self) -> dict[str, Any]:
        """给军种 LLM 的最小可调用说明，不暴露脚本实现。"""
        return {
            "skill_id": self.skill_id,
            "display_name": self.display_name,
            "description": self.description,
            "mission_type": self.mission_type,
            "unit_types": list(self.unit_types),
            "requires_area": self.requires_area,
            "area_type_id": self.area_type_id,
            "requires_target": self.requires_target,
            "target_kind": self.target_kind,
            "supports_escort": self.supports_escort,
            "supports_loadout": self.supports_loadout,
            "supports_routes": self.supports_routes,
            "rules": list(self.rules),
            "parameter_schema": self.parameter_schema,
        }


@dataclass(frozen=True)
class SkillCall:
    call_id: str
    skill_id: str
    mission_name: str
    unit_guids: tuple[str, ...]
    escort_unit_guids: tuple[str, ...] = ()
    target_contact_guids: tuple[str, ...] = ()
    loadout_guid: str = ""
    existing_mission_guid: str = ""
    area: dict[str, Any] | None = None
    routes: dict[str, Any] | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillCall":
        if not isinstance(data, dict):
            raise ValueError("skill_call 必须是对象")
        allowed = {
            "call_id", "skill_id", "mission_name", "unit_guids",
            "escort_unit_guids", "target_contact_guids", "loadout_guid",
            "existing_mission_guid", "area", "routes", "parameters", "reason",
        }
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"skill_call 含未知字段：{', '.join(unknown)}")
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("skill_call.parameters 必须是对象")
        return cls(
            call_id=str(data.get("call_id", "")).strip(),
            skill_id=str(data.get("skill_id", "")).strip(),
            mission_name=str(data.get("mission_name", "")).strip(),
            unit_guids=tuple(data.get("unit_guids") or ()),
            escort_unit_guids=tuple(data.get("escort_unit_guids") or ()),
            target_contact_guids=tuple(data.get("target_contact_guids") or ()),
            loadout_guid=str(data.get("loadout_guid", "")).strip(),
            existing_mission_guid=str(data.get("existing_mission_guid", "")).strip(),
            area=data.get("area"),
            routes=data.get("routes"),
            parameters=parameters,
            reason=str(data.get("reason", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "skill_id": self.skill_id,
            "mission_name": self.mission_name,
            "unit_guids": list(self.unit_guids),
            "escort_unit_guids": list(self.escort_unit_guids),
            "target_contact_guids": list(self.target_contact_guids),
            "loadout_guid": self.loadout_guid,
            "existing_mission_guid": self.existing_mission_guid,
            "area": self.area,
            "routes": self.routes,
            "parameters": self.parameters,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MissionInvocation:
    call: SkillCall
    spec: SkillSpec
    script_path: Path
    config_path: Path
    report_path: Path
    config: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ExecutionResult:
    call_id: str
    skill_id: str
    phase: str
    success: bool
    return_code: int
    stdout: str
    stderr: str
    config_path: str
    report_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "skill_id": self.skill_id,
            "phase": self.phase,
            "success": self.success,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "config_path": self.config_path,
            "report_path": self.report_path,
        }
