"""Offline checks for the deterministic jammer trigger and flight controller."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rules.jammer_support import follow_waypoint, is_jammer, striker_aircraft
from runtime.execution.jammer_controller import JammerEscortController

ROOT = Path(__file__).resolve().parents[1]

MISSION = "11111111-1111-4111-8111-111111111111"
JAMMER = "22222222-2222-4222-8222-222222222222"
STRIKER = "33333333-3333-4333-8333-333333333333"
SIDE = "44444444-4444-4444-8444-444444444444"


def situation(*, airborne=False):
    return {
        "api_base": "http://localhost:8050/ApiCtrl/api", "side": {"guid": SIDE},
        "scenario": {"start_time": "2026-11-06 08:00:00", "end_time": "2026-11-06 16:00:00"},
        "units": {"air": [
            {"guid": STRIKER, "name": "F-16", "lat": 15.0,
             "lon": 142.0 if not airborne else 142.2,
             "airborne": airborne, "in_facility": not airborne},
            {"guid": JAMMER, "name": "EC-130H", "lat": 15.0, "lon": 142.0,
             "airborne": airborne, "in_facility": not airborne},
        ]},
    }


def mission_detail(*, target=True):
    return {"strikeMissionData": {"targetList": ["target"] if target else []},
            "missionBasicData": {"assignedCombatUnit": [
                {"children": [{"key": STRIKER}]}]}}


class JammerSupportTest(unittest.TestCase):
    def test_flat_mission_assignment_marks_jammer_occupied(self):
        rows = [{"assignUnit": [{"id": JAMMER}]}]
        self.assertIn(JAMMER, JammerEscortController._occupied(rows))

    def test_jammer_never_becomes_striker(self):
        units = situation()["units"]["air"]
        detail = mission_detail()
        detail["missionBasicData"]["assignedCombatUnit"][0]["children"].append({"key": JAMMER})
        self.assertTrue(is_jammer(units[1]))
        self.assertEqual([STRIKER], [row["guid"] for row in striker_aircraft(detail, units)])
        self.assertEqual([], striker_aircraft(mission_detail(target=False), units))

    def test_waypoint_trails_eastbound_package(self):
        self.assertIsNone(follow_waypoint({"lat": 15, "lon": 142.2},
                                          {"lat": 15, "lon": 142.4}, None, 20))
        point = follow_waypoint({"lat": 15, "lon": 142.2},
                                {"lat": 15, "lon": 142}, (15, 142), 20)
        self.assertIsNotNone(point)
        self.assertLess(point["lon"], 142.2)

    def test_creation_triggers_one_idle_jammer_then_follows(self):
        with TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "script.ps1"
            path.write_text("# mock", encoding="utf-8")
            current = {"detail": mission_detail(), "occupied": False}
            calls = []

            def get_json(url):
                if "/MissionGant?" in url:
                    rows = ([{"assignUnit": [{"children": [{"id": JAMMER}]}]}]
                            if current["occupied"] else [])
                    return {"code": 0, "data": rows}
                if "/Mission?" in url:
                    return {"code": 0, "data": current["detail"]}
                if "/unitSensors?" in url:
                    return {"code": 0, "data": {"sensorList": [
                        {"type": "雷达"}, {"type": "ECM"}]}}
                raise AssertionError(url)

            with patch("runtime.execution.jammer_controller.find_powershell", return_value="pwsh"):
                controller = JammerEscortController(script_path=path, run_root=Path(temp),
                                                     get_json=get_json, clock=lambda: 100)
            controller._script = lambda _sit, _mis, _jam, mode, **kw: calls.append((mode, kw)) or {"success": True}
            state = {"missions": {"strike": {"guid": MISSION, "skill_id": "air.sea_strike"}}}
            controller.tick(situation(), state)
            self.assertEqual(["takeoff"], [mode for mode, _ in calls])
            self.assertEqual(JAMMER, state["jammer_support"][MISSION]["jammer_guid"])
            controller.tick(situation(), state)
            self.assertEqual(1, len(calls))  # no duplicate launch
            controller.tick(situation(airborne=True), state)
            self.assertEqual("follow", calls[-1][0])
            self.assertLess(calls[-1][1]["waypoint"]["lon"], 142.2)

    def test_no_launch_without_target_or_free_jammer(self):
        with TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "script.ps1"
            path.write_text("# mock", encoding="utf-8")
            occupied = [False]
            target = [False]

            def get_json(url):
                if "/MissionGant?" in url:
                    rows = ([{"assignUnit": [{"children": [{"id": JAMMER}]}]}]
                            if occupied[0] else [])
                    return {"code": 0, "data": rows}
                if "/Mission?" in url:
                    return {"code": 0, "data": mission_detail(target=target[0])}
                if "/unitSensors?" in url:
                    return {"code": 0, "data": {"sensorList": [
                        {"type": "雷达"}, {"type": "ECM"}]}}
                raise AssertionError(url)

            with patch("runtime.execution.jammer_controller.find_powershell", return_value="pwsh"):
                controller = JammerEscortController(script_path=path, run_root=Path(temp),
                                                     get_json=get_json)
            calls = []
            controller._script = lambda *args, **kw: calls.append(args)
            state = {"missions": {"strike": {"guid": MISSION, "skill_id": "air.air_strike"}}}
            controller.tick(situation(), state)
            target[0] = True
            occupied[0] = True
            controller.tick(situation(), state)
            self.assertEqual([], calls)

    def test_removed_strike_stops_airborne_jammer(self):
        with TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "script.ps1"
            path.write_text("# mock", encoding="utf-8")
            get_json = lambda url: {"code": 0, "data": []}
            with patch("runtime.execution.jammer_controller.find_powershell", return_value="pwsh"):
                controller = JammerEscortController(script_path=path, run_root=Path(temp),
                                                     get_json=get_json)
            calls = []
            controller._script = lambda _sit, _mis, _jam, mode, **kw: calls.append((mode, kw)) or {"success": True}
            state = {"missions": {}, "jammer_support": {
                MISSION: {"jammer_guid": JAMMER, "phase": "following"}}}
            controller.tick(situation(airborne=True), state)
            self.assertEqual([("finish", {"airborne": True})], calls)
            self.assertEqual({}, state["jammer_support"])


if __name__ == "__main__":
    unittest.main()
