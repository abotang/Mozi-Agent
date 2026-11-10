"""Read-only diagnosis of patrol/strike reuse of an already tasked aircraft.

No LLM request, POST, mission creation, deletion, or scenario mutation occurs.
When the current scenario has no assigned aircraft, a synthetic assignment is
added to an in-memory copy solely to exercise the same production validator.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer
from runtime.mozi.live_situation import collect_live_situation
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


def _guid(value) -> str:
    return str(value or "").strip("{}").lower()


def _check(plan: dict, situation: dict, registry: SkillRegistry) -> dict:
    try:
        validate_plan(plan, situation, registry)
        return {"accepted": True, "reason": ""}
    except Exception as exc:
        return {"accepted": False, "reason": f"{type(exc).__name__}: {exc}"}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="只读检查已占用飞机是否可被新巡逻/打击任务选择")
    parser.add_argument("--aircraft-guid", help="指定当前蓝方飞机 GUID；省略时自动选一架")
    parser.add_argument("--require-live-assignment", action="store_true",
                        help="没有真实已分配飞机时报告无法验证，不使用本地模拟占用")
    args = parser.parse_args(argv)
    deployment = json.loads((ROOT / "config" / "deployment.json").read_text(encoding="utf-8-sig"))
    situation = collect_live_situation(
        host=deployment["mozi_host"], ws_port=int(deployment["mozi_ws_port"]),
        api_base=deployment["mozi_api_base"])
    base = situation["api_base"].rstrip("/")
    with urlopen(f"{base}/MissionGant?SideGuid={situation['side']['guid']}", timeout=12) as response:
        raw = json.load(response)
    rows = raw.get("data") if isinstance(raw, dict) else None
    occupied = {_guid(guid): mission for guid, mission in situation["mission_assignments"].items()}
    aircraft = [unit for unit in situation["units"]["air"]
                if not is_awacs(unit) and not is_jammer(unit)
                and isinstance(unit.get("lat"), (float, int))
                and isinstance(unit.get("lon"), (float, int))]
    if args.aircraft_guid:
        aircraft = [unit for unit in aircraft if _guid(unit.get("guid")) == _guid(args.aircraft_guid)]
    if not aircraft:
        print(json.dumps({"result": "no_eligible_aircraft", "blue_aircraft": len(situation["units"]["air"]),
                          "writes_to_mozi": 0}, ensure_ascii=False, indent=2))
        return 2
    selected = next((unit for unit in aircraft if _guid(unit["guid"]) in occupied), aircraft[0])
    guid = _guid(selected["guid"])
    live = guid in occupied
    result = {"result": "checked" if live else "live_assignment_unavailable",
              "aircraft_guid": guid, "live_assigned": live,
              "mission_gant_code": raw.get("code") if isinstance(raw, dict) else None,
              "mission_gant_data_type": type(rows).__name__,
              "mission_gant_rows": len(rows) if isinstance(rows, list) else None,
              "live_occupied_aircraft": len(occupied), "writes_to_mozi": 0}
    if args.require_live_assignment and not live:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    # Both checks use the real validation code. The added assignment/target is
    # confined to a local Python dict and never sent to the Mozi API.
    context = deepcopy(situation)
    context["mission_assignments"] = {guid: occupied.get(guid) or str(uuid4())}
    lat, lon = float(selected["lat"]), float(selected["lon"])
    area = {"points": [{"lat": lat, "lon": lon},
                       {"lat": lat + 0.02, "lon": lon},
                       {"lat": lat + 0.02, "lon": lon + 0.02}]}
    patrol = {"version": 1, "skill_calls": [{"call_id": "diag-patrol",
              "skill_id": "air.combat_patrol", "mission_name": "只读诊断-新巡逻",
              "unit_guids": [guid], "area": area}]}
    contact = {"guid": str(uuid4()), "name": "仅供本地校验的模拟接触",
               "unit_type": "Aircraft", "lat": lat + 0.1, "lon": lon + 0.1}
    context["contacts"]["air"].append(contact)
    strike = {"version": 1, "skill_calls": [{"call_id": "diag-strike",
              "skill_id": "air.air_strike", "mission_name": "只读诊断-新打击",
              "unit_guids": [guid], "target_contact_guids": [contact["guid"]]}]}
    registry = SkillRegistry(ROOT / "skills").load()
    free = deepcopy(context)
    free["mission_assignments"] = {}
    result["free_patrol_control"] = _check(patrol, free, registry)
    result["free_strike_control"] = _check(strike, free, registry)
    result["occupied_patrol"] = _check(patrol, context, registry)
    result["occupied_strike"] = _check(strike, context, registry)
    result["all_checks_passed"] = (
        result["free_patrol_control"]["accepted"]
        and result["free_strike_control"]["accepted"]
        and not result["occupied_patrol"]["accepted"]
        and not result["occupied_strike"]["accepted"]
        and "飞机已被任务占用" in result["occupied_patrol"]["reason"]
        and "飞机已被任务占用" in result["occupied_strike"]["reason"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
