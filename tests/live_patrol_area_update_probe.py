"""One-shot live patrol area update probe with cleanup of both test areas."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live_aircraft_occupancy_probe import _cleanup, _find_task, _guid, _post
from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer
from runtime.execution.area_cleanup import OwnedAreaGarbageCollector
from runtime.execution.executor import PowerShellMissionExecutor
from runtime.execution.orchestrator import ScriptSkillOrchestrator
from runtime.mozi.live_situation import collect_live_situation
from runtime.skills.compiler import PlanCompiler
from runtime.skills.registry import SkillRegistry


def _area(name, lat, lon):
    return {"name": name, "points": [
        {"lat": lat, "lon": lon}, {"lat": lat + .04, "lon": lon},
        {"lat": lat + .04, "lon": lon + .04}, {"lat": lat, "lon": lon + .04}]}


def _run(orchestrator, situation, plan, run_id):
    result = orchestrator.execute_plan(plan, situation, dry_run=False, run_id=run_id)
    execution = result["results"][-1]
    task = _find_task(Path(execution["report_path"]), execution["stdout"])
    return task, execution["report_path"]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2 or sys.argv[1] != "--execute":
        print("用法：python tests/live_patrol_area_update_probe.py --execute（会创建并清理临时巡逻任务）")
        return 2
    deployment = json.loads((ROOT / "config/deployment.json").read_text(encoding="utf-8-sig"))
    source = lambda: collect_live_situation(
        host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
        api_base=deployment["mozi_api_base"])
    situation = source()
    candidates = [unit for unit in situation["units"]["air"]
                  if unit.get("in_facility") is True and not is_awacs(unit)
                  and not is_jammer(unit)
                  and _guid(unit.get("guid")) not in situation["mission_assignments"]
                  and isinstance(unit.get("lat"), (int, float))
                  and isinstance(unit.get("lon"), (int, float))]
    if not candidates:
        print(json.dumps({"success": False, "error": "没有空闲战斗机"}, ensure_ascii=False))
        return 2
    unit = candidates[0]
    registry = SkillRegistry(ROOT / "skills").load()
    orchestrator = ScriptSkillOrchestrator(
        registry, PlanCompiler(registry, ROOT / "scripts/powershell", ROOT / "runs"),
        PowerShellMissionExecutor())
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "Codex-临时改区测试-" + suffix[-6:]
    lat, lon = float(unit["lat"]), float(unit["lon"])
    call = {"call_id": "area-a", "skill_id": "air.combat_patrol",
            "mission_name": name, "unit_guids": [unit["guid"]],
            "area": _area(name + "-A", lat, lon)}
    mission_guid = ""
    areas = []
    result = {"aircraft_guid": unit["guid"], "mission_name": name,
              "created": False, "updated": False, "area_readback_matched": False,
              "collector_old_deleted": False, "collector_new_retained": False,
              "cleanup": {}, "reports": []}
    try:
        first, report = _run(orchestrator, situation,
                             {"version": 1, "skill_calls": [deepcopy(call)]},
                             "patrol-area-probe-" + suffix + "-a")
        result["reports"].append(report)
        mission_guid = str(first.get("missionGuid") or "")
        area_a = str((first.get("area") or {}).get("guid") or "")
        if area_a:
            areas.append(area_a)
        result["created"] = bool(mission_guid and area_a)
        if not result["created"]:
            raise RuntimeError("首次创建未返回任务和区域 GUID")
        current = source()
        if _guid(current["mission_assignments"].get(_guid(unit["guid"]))) != _guid(mission_guid):
            raise RuntimeError("创建后没有回读到飞机属于原巡逻任务")

        updated_call = deepcopy(call)
        updated_call.update(call_id="area-b", existing_mission_guid=mission_guid,
                            area=_area(name + "-B", lat + .12, lon + .12))
        second, report = _run(orchestrator, current,
                              {"version": 1, "skill_calls": [updated_call]},
                              "patrol-area-probe-" + suffix + "-b")
        result["reports"].append(report)
        area_b = str((second.get("area") or {}).get("guid") or "")
        if area_b:
            areas.append(area_b)
        result["updated"] = bool(area_b and _guid(second.get("missionGuid")) == _guid(mission_guid)
                                 and _guid(area_b) != _guid(area_a))
        if not result["updated"]:
            raise RuntimeError("更新后未保持原任务 GUID 或未生成新区")
        with urlopen(situation["api_base"].rstrip("/") + "/Mission?Guid=" + quote(mission_guid),
                     timeout=12) as response:
            detail = json.load(response)
        points = ((detail.get("data") or {}).get("patrolmissionData") or {}).get("PatrolArea") or []
        live_refs = {_guid(point.get("id")) for point in points if isinstance(point, dict)}
        expected_refs = {_guid(value) for value in (second.get("area") or {}).get("reference", {}).get("data", [])}
        result["area_readback_matched"] = bool(expected_refs and live_refs == expected_refs)
        result["mission_guid_unchanged"] = _guid(second.get("missionGuid")) == _guid(mission_guid)
        # Scope the production garbage collector to these two test reports.
        with TemporaryDirectory() as folder:
            for index, source_report in enumerate(result["reports"]):
                destination = Path(folder) / f"run-{index}" / "reports" / Path(source_report).name
                destination.parent.mkdir(parents=True)
                shutil.copyfile(source_report, destination)
            collected = OwnedAreaGarbageCollector(folder).collect(situation)
        result["collector"] = collected
        result["collector_old_deleted"] = _guid(area_a) in collected["deleted"]
        result["collector_new_retained"] = _guid(area_b) in collected["linked"]
        if not (result["collector_old_deleted"] and result["collector_new_retained"]):
            raise RuntimeError("原巡逻任务换区后旧区域未被清理，或新区未保留")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:1100]
    finally:
        # Delete the task with its latest area, then remove the old unlinked area.
        latest_area = areas[-1] if areas else ""
        result["cleanup"]["mission_and_latest_area"] = (
            _cleanup(situation["api_base"], situation["side"], mission_guid, latest_area)
            if mission_guid else {"success": False, "error": "未获得任务 GUID"})
        for old_area in areas[:-1]:
            if result["collector_old_deleted"]:
                continue
            try:
                response = _post(situation["api_base"], "ScenArea", {
                    "option": "delArea", "side": situation["side"]["name"],
                    "area_id": _guid(old_area)})
                result["cleanup"]["old_area_code"] = response.get("code")
            except Exception as exc:
                result["cleanup"]["old_area_error"] = str(exc)[:400]
        try:
            latest = source()
            aircraft = next((row for row in latest["units"]["air"]
                             if _guid(row.get("guid")) == _guid(unit["guid"])), None)
            if aircraft and aircraft.get("airborne") is True:
                response = _post(situation["api_base"], "air-operations/aircraft-status", {
                    "id": [unit["guid"]], "type": "terminationOfOperation", "params": ""})
                result["cleanup"]["return_request_code"] = response.get("code")
            with urlopen(situation["api_base"].rstrip("/") + "/Mission?Guid=" + quote(mission_guid),
                         timeout=12) as response:
                mission_response = json.load(response)
            result["cleanup"]["mission_absent"] = not bool(mission_response.get("data"))
            with urlopen(situation["api_base"].rstrip("/") + "/ScenArea?type=" + quote(situation["side"]["name"]),
                         timeout=12) as response:
                area_response = json.load(response)
            serialized = json.dumps(area_response, ensure_ascii=False).lower()
            result["cleanup"]["areas_absent"] = all(_guid(area) not in serialized for area in areas)
        except Exception as exc:
            result["cleanup"]["verification_error"] = str(exc)[:400]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (result["created"] and result["updated"] and result["area_readback_matched"]
                 and result["collector_old_deleted"] and result["collector_new_retained"]
                 and result["cleanup"].get("mission_absent") and result["cleanup"].get("areas_absent")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
