"""Read-only Mozi area inventory used to protect aircraft station spacing."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.mozi.live_situation import collect_live_situation
from runtime.mozi.patrol_areas import read_blue_patrol_areas


SIDE = "11111111-1111-1111-1111-111111111111"
CAP = "22222222-2222-2222-2222-222222222222"
OLD = "33333333-3333-3333-3333-333333333333"
NEW = "44444444-4444-4444-4444-444444444444"
AWACS = "55555555-5555-5555-5555-555555555555"
SUPPORT = "66666666-6666-6666-6666-666666666666"
SHIP = "77777777-7777-7777-7777-777777777777"
SHIP_AREA = "88888888-8888-8888-8888-888888888888"


def _area(guid: str, mission: str, *, points=None) -> dict:
    return {"id": "{" + guid + "}", "name": guid[:8],
            "missionList": [{"id": mission}],
            "points": points if points is not None else [
                {"latitude": 22.0, "longitude": 121.0},
                {"latitude": 22.1, "longitude": 121.0},
                {"latitude": 22.0, "longitude": 121.1},
            ]}


def _responses():
    inventory = {"code": 0, "data": {"sideAreas": [{
        "guid": SIDE, "areaTypes": [
            {"id": "405", "areas": [_area(OLD, CAP), _area(NEW, CAP),
                                     _area(SHIP_AREA, SHIP)]},
            {"id": "407", "areas": [_area(AWACS, SUPPORT)]},
        ],
    }]}}
    missions = {
        CAP: {"code": 0, "data": {
            "missionBasicData": {"name": "空战巡逻", "classify": "空战巡逻"},
            "patrolmissionData": {"PatrolArea_ID": "{" + NEW + "}"}}},
        SUPPORT: {"code": 0, "data": {
            "missionBasicData": {"name": "预警", "type": "Support"},
            "supportmissionData": {"area_ID": "{" + AWACS + "}"}}},
        SHIP: {"code": 0, "data": {
            "missionBasicData": {"name": "海巡", "classify": "海上巡逻"},
            "patrolmissionData": {"PatrolArea_ID": "{" + SHIP_AREA + "}"}}},
    }

    def get_json(url: str):
        if url.endswith("/ScenArea?type=all"):
            return inventory
        return missions[url.split("Guid=", 1)[1]]

    return inventory, missions, get_json


class PatrolAreaReadTests(unittest.TestCase):
    def test_only_current_air_cap_and_awacs_areas_are_returned(self):
        _, _, get_json = _responses()
        areas = read_blue_patrol_areas("http://mozi/api", SIDE, get_json=get_json)
        self.assertEqual([item["guid"] for item in areas], [NEW, AWACS])
        self.assertEqual([item["type_id"] for item in areas], ["405", "407"])
        self.assertEqual([item["kind"] for item in areas],
                         ["combat_patrol", "early_warning"])
        self.assertEqual(areas[0]["points"][0], {"lat": 22.0, "lon": 121.0})
        self.assertEqual(areas[0]["mission_guids"], [CAP])

    def test_failed_mission_read_cannot_hide_an_occupied_area(self):
        _, missions, get_json = _responses()
        missions[CAP] = {"code": 101, "data": None}
        with self.assertRaisesRegex(RuntimeError, "详情读取失败"):
            read_blue_patrol_areas("http://mozi/api", SIDE, get_json=get_json)

    def test_invalid_current_polygon_remains_visible_for_patrol_guard(self):
        inventory, _, get_json = _responses()
        inventory["data"]["sideAreas"][0]["areaTypes"][0]["areas"][1]["points"][0]["latitude"] = "bad"
        areas = read_blue_patrol_areas("http://mozi/api", SIDE, get_json=get_json)
        self.assertEqual([item["guid"] for item in areas], [NEW, AWACS])
        self.assertEqual(areas[0]["mission_guids"], [CAP])
        self.assertEqual(areas[0]["points"], [])
        self.assertIn("坐标无效", areas[0]["geometry_error"])
        self.assertIsNone(areas[1]["geometry_error"])

    def test_live_situation_exposes_read_only_area_inventory(self):
        areas = [{"guid": NEW, "type_id": "405", "points": []}]
        frame = {"side": [{"id": SIDE, "name": "蓝方", "activeUnits": [],
                            "contacts": []}], "FightTree": []}
        with (patch("runtime.mozi.live_situation._frame", return_value=frame),
              patch("runtime.mozi.live_situation._times", return_value=("now", "end", "start")),
              patch("runtime.mozi.live_situation.read_blue_mission_assignments", return_value={}),
              patch("runtime.mozi.live_situation.read_blue_patrol_areas", return_value=areas)):
            situation = collect_live_situation()
        self.assertIs(situation["existing_areas"], areas)


if __name__ == "__main__":
    unittest.main()
