"""独立读取墨子 WebSocket 态势和 REST 想定时间。"""

from datetime import datetime, timedelta
import json
from urllib.request import urlopen
import zlib

from runtime.contracts import SERVICE_UNIT_TYPES


def _frame(host, port):
    try:
        from websocket import create_connection
    except ImportError as exc:
        raise RuntimeError("请安装 websocket-client 以读取墨子实时态势") from exc
    ws = create_connection(f"ws://{host}:{port}/ws", timeout=20)
    try:
        for _ in range(5):
            raw = ws.recv()
            if isinstance(raw, bytes):
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except zlib.error:
                    raw = zlib.decompress(raw)
            state = json.loads(raw)
            if isinstance(state, dict) and isinstance(state.get("side"), list):
                return state
    finally:
        ws.close()
    raise RuntimeError("WebSocket 未返回有效态势帧")


def _item(row, *, airborne=None):
    return {"guid": row.get("id", ""), "name": row.get("name", ""),
            "unit_type": row.get("UnitType", ""), "sub_type": row.get("subType", ""),
            "lat": row.get("Latitude", row.get("lat")),
            "lon": row.get("Longitude", row.get("lon")),
            "speed": row.get("CurrentSpeed"), "damage": row.get("Damage"),
            "fuel": row.get("FuelRec"), "airborne": airborne,
            "in_facility": row.get("AirCraftInFacility") is True}


def _times(api_base):
    with urlopen(f"{api_base.rstrip('/')}/GetScenarioTime", timeout=10) as response:
        payload = json.load(response)
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError("GetScenarioTime 读取失败")
    data = payload["data"]
    current = str(data.get("CurrentTime") or data.get("StartTime") or "").strip()
    if not current:
        raise RuntimeError("想定时间为空")
    end = str(data.get("EndTime") or "").strip()
    if not end:
        duration = str(data.get("DurationTime") or "").split("-")
        if len(duration) != 3 or not all(part.isdigit() for part in duration):
            raise RuntimeError("想定缺少有效 EndTime/DurationTime")
        days, hours, minutes = map(int, duration)
        scenario_start = str(data.get("StartTime") or current)
        end = (datetime.fromisoformat(scenario_start) + timedelta(
            days=days, hours=hours, minutes=minutes)).isoformat(sep=" ")
    return (current.replace("T", " "), end.replace("T", " "),
            str(data.get("StartTime") or current).replace("T", " "))


def collect_live_situation(*, host="127.0.0.1", rest_port=8082,
                           ws_port=8050,
                           api_base="http://127.0.0.1:8050/ApiCtrl/api"):
    state = _frame(host, ws_port)
    side = next((row for row in state["side"] if isinstance(row, dict)
                 and "蓝" in str(row.get("name", ""))), None)
    if side is None or not side.get("id"):
        raise RuntimeError("无法确认蓝方及其 GUID")
    active = [row for row in side.get("activeUnits", []) if isinstance(row, dict)]
    air = [_item(row, airborne=True) for row in active if row.get("UnitType") == "Aircraft"]
    seen = {row["guid"] for row in air}
    for group in state.get("FightTree", []):
        if not isinstance(group, dict) or group.get("SideID") != side["id"]:
            continue
        for row in group.get("FightTree", []):
            if (isinstance(row, dict) and row.get("UnitType") == "Aircraft"
                    and row.get("AirCraftInFacility") is True and row.get("id") not in seen):
                air.append(_item(row, airborne=False))
                seen.add(row["id"])
    navy = [_item(row) for row in active if row.get("UnitType") in SERVICE_UNIT_TYPES["navy"]]
    army = [_item(row) for row in active if row.get("UnitType") in SERVICE_UNIT_TYPES["army"]]
    submarine = [_item(row) for row in active if row.get("UnitType") == "Submarine"]
    categories = {"Aircraft": "air", "Ship": "sea", "Facility": "land",
                  "Submarine": "submarine"}
    contacts = {key: [] for key in ("air", "sea", "land", "submarine")}
    blue_ids = {row.get("id") for row in active}
    for row in side.get("contacts", []):
        if not isinstance(row, dict) or not row.get("id"):
            continue
        actual = row.get("actualUnitId") or row.get("ActualUnitID") or row.get("actualUnitGuid")
        if actual and actual in blue_ids:
            continue
        if not actual and str(row.get("SideColor", "")).lower() != "#ff0000":
            continue
        category = categories.get(row.get("UnitType"))
        if category:
            contacts[category].append(_item(row))
    start, end, initial = _times(api_base)
    return {"api_base": api_base, "side": {"name": side["name"], "guid": side["id"]},
            "scenario": {"name": str(state.get("ScenarioName") or ""),
                         "start_time": start, "end_time": end,
                         "initial_time": initial},
            "units": {"air": air, "navy": navy, "army": army,
                      "submarine": submarine},
            "contacts": contacts, "existing_missions": [], "existing_areas": []}
