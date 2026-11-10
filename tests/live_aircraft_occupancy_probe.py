"""Create one temporary blue patrol, test occupancy, then delete it and its area.

The strike check is local validation only: it never creates a strike mission or
assigns a real target. Run explicitly with ``--execute`` to mutate the scenario.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer
from runtime.execution.executor import PowerShellMissionExecutor, find_powershell
from runtime.execution.orchestrator import ScriptSkillOrchestrator
from runtime.mozi.live_situation import collect_live_situation
from runtime.mozi.mission_assignments import read_blue_mission_assignments
from runtime.planning.validation import validate_plan
from runtime.skills.compiler import PlanCompiler
from runtime.skills.registry import SkillRegistry


def _post(base: str, path: str, body: dict) -> dict:
    request = Request(f"{base.rstrip('/')}/{path}",
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"},
                      method="POST")
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def _guid(value) -> str:
    return str(value or "").strip("{}").lower()


def _local_check(plan: dict, situation: dict, registry: SkillRegistry) -> dict:
    try:
        validate_plan(plan, situation, registry)
        return {"accepted": True, "error": ""}
    except Exception as exc:
        return {"accepted": False, "error": f"{type(exc).__name__}: {exc}"}


def _find_task(report_path: Path, stdout: str) -> dict:
    for source in (report_path,):
        if source.is_file():
            try:
                tasks = json.loads(source.read_text(encoding="utf-8-sig")).get("tasks") or []
                if tasks:
                    return tasks[0]
            except (OSError, ValueError, AttributeError):
                pass
    try:
        tasks = json.loads(stdout).get("tasks") or []
        return tasks[0] if tasks else {}
    except (ValueError, AttributeError, IndexError):
        return {}


def _cleanup(base: str, side: dict, mission_guid: str, area_guid: str) -> dict:
    if not mission_guid:
        return {"success": False, "error": "未取得任务 GUID，无法自动删除"}
    command = [find_powershell(), "-NoProfile", "-NonInteractive", "-File",
               str(ROOT / "scripts/powershell/08-删除任务及对应区域.ps1"),
               "-MissionGuid", mission_guid, "-SideGuid", side["guid"],
               "-SideName", side["name"], "-ApiBase", base]
    if area_guid:
        command += ["-AreaGuid", area_guid]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=80,
                                   check=False, shell=False)
        payload = json.loads(completed.stdout.strip().lstrip("\ufeff"))
        if payload.get("missionDeleted") and (not area_guid or payload.get("areaDeleted")):
            return {"success": True, "method": "08-删除任务及对应区域.ps1"}
        script_error = str(payload.get("error") or completed.stderr)[:500]
    except Exception as exc:
        script_error = str(exc)[:500]
    # Last-resort cleanup uses the same two documented API operations as 08.
    result = {"success": False, "method": "direct_cleanup", "script_error": script_error}
    try:
        response = _post(base, "MissionGant", {
            "SideGuid": _guid(side["guid"]), "MissionGuid": _guid(mission_guid),
            "MissionCommandType": "DeleteMission"})
        result["mission_delete_code"] = response.get("code")
        if area_guid:
            response = _post(base, "ScenArea", {
                "option": "delArea", "side": side["name"], "area_id": _guid(area_guid)})
            result["area_delete_code"] = response.get("code")
        result["success"] = (result["mission_delete_code"] == 0 and
                             (not area_guid or result.get("area_delete_code") == 0))
    except Exception as exc:
        result["error"] = str(exc)[:500]
    return result


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="一次性实测：临时巡逻占用 → 本地新巡逻/打击校验 → 清理")
    parser.add_argument("--execute", action="store_true", help="明确执行临时任务创建与删除")
    args = parser.parse_args(argv)
    if not args.execute:
        print("未执行：必须显式传入 --execute；该脚本会临时创建并删除一个蓝方巡逻任务和区域。")
        return 2

    deployment = json.loads((ROOT / "config/deployment.json").read_text(encoding="utf-8-sig"))
    situation = collect_live_situation(
        host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
        api_base=deployment["mozi_api_base"])
    candidates = [unit for unit in situation["units"]["air"]
                  if unit.get("in_facility") is True and not is_awacs(unit)
                  and not is_jammer(unit)
                  and _guid(unit.get("guid")) not in situation["mission_assignments"]
                  and isinstance(unit.get("lat"), (int, float))
                  and isinstance(unit.get("lon"), (int, float))]
    if not candidates:
        print(json.dumps({"success": False, "error": "没有可用的在设施内蓝方战斗机"},
                         ensure_ascii=False, indent=2))
        return 2
    aircraft = candidates[0]
    run_id = "occupancy-probe-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    mission_name = "Codex-临时占用测试-" + run_id[-6:]
    lat, lon = float(aircraft["lat"]), float(aircraft["lon"])
    area = {"name": mission_name + "-区域", "points": [
        {"lat": lat, "lon": lon}, {"lat": lat + 0.04, "lon": lon},
        {"lat": lat + 0.04, "lon": lon + 0.04}, {"lat": lat, "lon": lon + 0.04}]}
    registry = SkillRegistry(ROOT / "skills").load()
    plan = {"version": 1, "skill_calls": [{
        "call_id": "temporary-cap", "skill_id": "air.combat_patrol",
        "mission_name": mission_name, "unit_guids": [aircraft["guid"]], "area": area}]}
    compiler = PlanCompiler(registry, ROOT / "scripts/powershell", ROOT / "runs")
    executor = PowerShellMissionExecutor()
    invocations = compiler.compile(validate_plan(plan, situation, registry), situation, run_id)
    report_path = invocations[0].report_path
    mission_guid = area_guid = ""
    result = {"temporary_aircraft": aircraft["guid"], "temporary_mission_name": mission_name,
              "created": False, "cleanup": None, "strike_created": False}
    try:
        executions = executor.execute(invocations, dry_run=False)
        task = _find_task(report_path, executions[-1].stdout)
        mission_guid = str(task.get("missionGuid") or "")
        area_guid = str((task.get("area") or {}).get("guid") or "")
        result.update(created=bool(mission_guid), mission_guid=mission_guid, area_guid=area_guid)
        fresh = collect_live_situation(
            host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
            api_base=deployment["mozi_api_base"])
        assignment = fresh["mission_assignments"].get(_guid(aircraft["guid"]))
        result["mission_gant_assigned_mission"] = assignment
        result["live_assignment_matched"] = _guid(assignment) == _guid(mission_guid)
        if not result["live_assignment_matched"]:
            return 1

        new_patrol = deepcopy(plan)
        new_patrol["skill_calls"][0]["call_id"] = "occupied-cap"
        new_patrol["skill_calls"][0]["mission_name"] = mission_name + "-第二巡逻"
        contact = {"guid": str(uuid4()), "name": "仅供本地校验的模拟目标",
                   "unit_type": "Aircraft", "lat": lat + 0.1, "lon": lon + 0.1}
        fresh["contacts"]["air"].append(contact)
        new_strike = {"version": 1, "skill_calls": [{
            "call_id": "occupied-strike", "skill_id": "air.air_strike",
            "mission_name": mission_name + "-模拟打击", "unit_guids": [aircraft["guid"]],
            "target_contact_guids": [contact["guid"]]}]}
        result["new_patrol"] = _local_check(new_patrol, fresh, registry)
        result["new_strike"] = _local_check(new_strike, fresh, registry)
        result["blocked_both"] = (not result["new_patrol"]["accepted"]
                                  and not result["new_strike"]["accepted"]
                                  and "飞机已被任务占用" in result["new_patrol"]["error"]
                                  and "飞机已被任务占用" in result["new_strike"]["error"])
        return 0 if result["blocked_both"] else 1
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:1200]
        task = _find_task(report_path, "")
        mission_guid = mission_guid or str(task.get("missionGuid") or "")
        area_guid = area_guid or str((task.get("area") or {}).get("guid") or "")
        return 1
    finally:
        result["cleanup"] = _cleanup(situation["api_base"], situation["side"],
                                     mission_guid, area_guid) if mission_guid else {
                                         "success": False, "error": "未获得任务 GUID；检查执行报告与区域孤立记录"}
        if mission_guid:
            try:
                latest = collect_live_situation(
                    host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
                    api_base=deployment["mozi_api_base"])
                current = next((unit for unit in latest["units"]["air"]
                                if _guid(unit.get("guid")) == _guid(aircraft["guid"])), None)
                if current and current.get("airborne") is True:
                    return_response = _post(situation["api_base"], "air-operations/aircraft-status", {
                        "id": [aircraft["guid"]], "type": "terminationOfOperation", "params": ""})
                    result["return_request_code"] = return_response.get("code")
            except Exception as exc:
                result["return_request_error"] = str(exc)[:400]
        result["report_path"] = str(report_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["cleanup"].get("success"):
            raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
