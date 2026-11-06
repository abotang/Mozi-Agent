"""把经过校验的 SkillCall 编译为七类 PowerShell 脚本配置。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from runtime.contracts import MissionInvocation, SkillCall
from runtime.skills.registry import SkillRegistry


_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


def _safe(value: str) -> str:
    cleaned = _SAFE_NAME.sub("-", value).strip("-.")
    return cleaned[:80] or "call"


def _empty_task(spec) -> dict[str, Any]:
    return {
        "enabled": False,
        "name": spec.display_name,
        "missionType": spec.mission_type,
        "classify": spec.classify,
        "existingMissionGuid": "",
        "unitGuids": [],
        "escortUnitGuids": [],
        "targetContactGuids": [],
        "targetKind": spec.target_kind or "Aircraft",
        "loadoutGuid": "",
        "area": None,
        "routes": None,
        "parameters": {},
    }


def _compile_area(call: SkillCall, area_type_id: str) -> dict[str, Any]:
    area = call.area or {}
    return {
        "existingAreaGuid": str(area.get("existing_area_guid", "")).strip(),
        "typeId": area_type_id,
        "name": str(area.get("name") or f"{call.mission_name}-区域"),
        "remark": str(area.get("remark") or f"{call.mission_name}任务区域"),
        "points": [
            {"lon": point["lon"], "lat": point["lat"]}
            for point in area.get("points", [])
        ],
    }


def _compile_route(route: dict[str, Any] | None, default_name: str) -> dict[str, Any] | None:
    if route is None:
        return None
    return {
        "existingRouteGuid": str(route.get("existing_route_guid", "")).strip(),
        "name": str(route.get("name") or default_name),
        "throttle": str(route.get("throttle", "2")),
        "show": bool(route.get("show", True)),
        "points": [
            {"lon": point["lon"], "lat": point["lat"]}
            for point in route.get("points", [])
        ],
    }


class PlanCompiler:
    def __init__(self, registry: SkillRegistry, scripts_root: str | Path, run_root: str | Path):
        self.registry = registry
        self.scripts_root = Path(scripts_root).resolve()
        self.run_root = Path(run_root).resolve()

    def compile(self, calls: list[SkillCall], situation: dict[str, Any], run_id: str | None = None) -> list[MissionInvocation]:
        stamp = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.run_root / _safe(stamp)
        config_dir = run_dir / "configs"
        report_dir = run_dir / "reports"
        invocations = []
        all_specs = self.registry.all()
        for sequence, call in enumerate(calls, 1):
            spec = self.registry.get(call.skill_id)
            tasks = {item.task_key: _empty_task(item) for item in all_specs}
            task = tasks[spec.task_key]
            task.update({
                "enabled": True,
                "name": call.mission_name,
                "existingMissionGuid": call.existing_mission_guid,
                "unitGuids": list(call.unit_guids),
                "escortUnitGuids": list(call.escort_unit_guids),
                "targetContactGuids": list(call.target_contact_guids),
                "targetKind": spec.target_kind or "Aircraft",
                "loadoutGuid": call.loadout_guid,
                "parameters": deepcopy(call.parameters),
            })
            if spec.requires_area:
                task["area"] = _compile_area(call, spec.area_type_id or "")
            if call.routes is not None:
                task["routes"] = {
                    "outbound": _compile_route(call.routes.get("outbound"), f"{call.mission_name}-出航航线"),
                    "onStation": _compile_route(call.routes.get("on_station"), f"{call.mission_name}-区内航线"),
                }
            config_path = config_dir / f"{sequence:03d}-{_safe(call.call_id)}.json"
            report_path = report_dir / f"{sequence:03d}-{_safe(call.call_id)}.json"
            config = {
                "schemaVersion": 1,
                "apiBase": situation.get("api_base", "http://127.0.0.1:8050/ApiCtrl/api"),
                "sideName": situation["side"]["name"],
                "sideGuid": situation["side"]["guid"],
                "startTime": situation["scenario"]["start_time"],
                "endTime": situation["scenario"]["end_time"],
                "reportPath": str(report_path),
                "tasks": tasks,
            }
            script_path = (self.scripts_root / spec.script).resolve()
            if script_path.parent != self.scripts_root or not script_path.is_file():
                raise ValueError(f"Skill 脚本不存在或越界：{spec.script}")
            invocations.append(MissionInvocation(
                call=call,
                spec=spec,
                script_path=script_path,
                config_path=config_path,
                report_path=report_path,
                config=deepcopy(config),
            ))
        return invocations

    @staticmethod
    def materialize(invocations: list[MissionInvocation]) -> None:
        for item in invocations:
            item.config_path.parent.mkdir(parents=True, exist_ok=True)
            item.report_path.parent.mkdir(parents=True, exist_ok=True)
            item.config_path.write_text(
                json.dumps(item.config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
