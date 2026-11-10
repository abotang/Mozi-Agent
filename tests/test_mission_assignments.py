"""Mission ownership parser tests; no real Mozi connection is opened."""

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch

from runtime.mozi.mission_assignments import read_blue_mission_assignments


SIDE = "11111111-1111-4111-8111-111111111111"
TASK_1 = "22222222-2222-4222-8222-222222222222"
TASK_2 = "33333333-3333-4333-8333-333333333333"
AIR_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
AIR_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SHIP = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def read(payload, aircraft=None):
    stream = BytesIO(json.dumps(payload).encode("utf-8"))
    with patch("runtime.mozi.mission_assignments.urlopen", return_value=stream) as mocked:
        result = read_blue_mission_assignments(
            "http://127.0.0.1:8050/ApiCtrl/api", SIDE, aircraft or {AIR_1, AIR_2})
    mocked.assert_called_once()
    return result


class MissionAssignmentsTests(TestCase):
    def test_null_data_is_empty(self):
        self.assertEqual({}, read({"code": 0, "data": None}))

    def test_flat_and_grouped_assignments(self):
        rows = [
            {"guid": TASK_1, "assignUnit": [{"id": AIR_1}, {"id": SHIP}]},
            {"missionGuid": TASK_2, "assignUnit": [
                {"key": SHIP, "children": [{"key": AIR_2}]}]},
        ]
        self.assertEqual({AIR_1: TASK_1, AIR_2: TASK_2}, read({"code": 0, "data": rows}))

    def test_singleton_group_key(self):
        rows = [{"id": TASK_1, "assignUnit": [{"key": AIR_1, "children": ""},
                                                {"key": AIR_1, "children": []}]}]
        self.assertEqual({AIR_1: TASK_1}, read({"code": 0, "data": rows}))

    def test_bad_api_or_untrusted_shape_fails_closed(self):
        for payload in (
            {"code": 101, "data": None},
            {"code": 0},
            {"code": 0, "data": {}},
            {"code": 0, "data": [{"guid": TASK_1}]},
            {"code": 0, "data": [{"guid": TASK_1, "assignUnit": "bad"}]},
            {"code": 0, "data": [{"guid": TASK_1, "assignUnit": [{"children": "bad"}]}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                read(payload)

    def test_same_aircraft_in_two_missions_fails_closed(self):
        rows = [{"guid": TASK_1, "assignUnit": [{"id": AIR_1}]},
                {"guid": TASK_2, "assignUnit": [{"id": AIR_1}]}]
        with self.assertRaisesRegex(RuntimeError, "同时出现在任务"):
            read({"code": 0, "data": rows})

    def test_http_failure_fails_closed(self):
        with patch("runtime.mozi.mission_assignments.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "不能判定飞机空闲"):
                read_blue_mission_assignments("http://127.0.0.1:8050/ApiCtrl/api",
                                              SIDE, {AIR_1})
