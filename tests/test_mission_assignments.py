"""Mission ownership parser tests; no real Mozi connection is opened."""

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

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

    def test_missing_list_assignment_reads_mission_detail(self):
        summary = {"code": 0, "data": [
            {"id": TASK_1, "name": "预警", "type": "Support"},
            {"id": TASK_2, "assignUnit": [{"id": AIR_2}]},
        ]}
        detail = {"code": 0, "data": {"missionBasicData": {
            "assignedCombatUnit": [{"key": SHIP, "children": [{"key": AIR_1}]}]
        }}}
        urls = []

        def fetch(url, timeout):
            urls.append(url)
            parsed = urlparse(url)
            if parsed.path.endswith("/MissionGant"):
                return BytesIO(json.dumps(summary).encode("utf-8"))
            if parsed.path.endswith("/Mission"):
                self.assertEqual(parse_qs(parsed.query)["Guid"], [TASK_1])
                return BytesIO(json.dumps(detail).encode("utf-8"))
            self.fail(f"unexpected URL: {url}")

        with patch("runtime.mozi.mission_assignments.urlopen", side_effect=fetch):
            result = read_blue_mission_assignments("http://127.0.0.1:8050/ApiCtrl/api",
                                                   SIDE, {AIR_1, AIR_2})
        self.assertEqual({AIR_1: TASK_1, AIR_2: TASK_2}, result)
        self.assertEqual(2, len(urls))

    def test_missing_list_assignment_with_empty_detail_is_unassigned(self):
        summary = {"code": 0, "data": [{"id": TASK_1}]}
        detail = {"code": 0, "data": {"missionBasicData": {"assignedCombatUnit": []}}}
        responses = [summary, detail]
        with patch("runtime.mozi.mission_assignments.urlopen",
                   side_effect=lambda url, timeout: BytesIO(
                       json.dumps(responses.pop(0)).encode("utf-8"))):
            self.assertEqual({}, read_blue_mission_assignments(
                "http://127.0.0.1:8050/ApiCtrl/api", SIDE, {AIR_1}))

    def test_missing_list_assignment_without_detail_stays_blocked(self):
        summary = {"code": 0, "data": [{"id": TASK_1}]}
        detail = {"code": 0, "data": {"missionBasicData": {}}}
        responses = [summary, detail]
        with patch("runtime.mozi.mission_assignments.urlopen",
                   side_effect=lambda url, timeout: BytesIO(
                       json.dumps(responses.pop(0)).encode("utf-8"))):
            with self.assertRaisesRegex(RuntimeError, "不能判定飞机空闲"):
                read_blue_mission_assignments("http://127.0.0.1:8050/ApiCtrl/api",
                                              SIDE, {AIR_1})

    def _read_coverage(self, summary, detail):
        responses = [summary, detail]
        sink = set()
        with patch("runtime.mozi.mission_assignments.urlopen",
                   side_effect=lambda url, timeout: BytesIO(
                       json.dumps(responses.pop(0)).encode("utf-8"))) as mocked:
            assignments = read_blue_mission_assignments(
                "http://127.0.0.1:8050/ApiCtrl/api", SIDE, {AIR_1, AIR_2},
                active_strike_target_guids=sink)
        return assignments, sink, mocked.call_count

    def test_coverage_reuses_assignment_detail_and_normalizes_targets(self):
        target = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        summary = {"code": 0, "data": [{"id": TASK_1, "type": "Strike"}]}
        detail = {"code": 0, "data": {
            "missionBasicData": {"type": "Strike", "assignedCombatUnit": [
                {"key": SHIP, "children": [{"key": AIR_1}, {"key": AIR_2}]}]},
            "strikeMissionData": {"targetList": [
                {"id": "{" + target.upper() + "}"}, {"id": target}]}}}
        assignments, targets, count = self._read_coverage(summary, detail)
        self.assertEqual(assignments, {AIR_1: TASK_1, AIR_2: TASK_1})
        self.assertEqual(targets, {target})
        self.assertEqual(count, 2)  # list + one reused detail, no duplicate GET

    def test_coverage_reads_strike_detail_even_when_summary_has_units(self):
        target = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        summary = {"code": 0, "data": [{"id": TASK_1, "type": "Strike",
                                           "assignUnit": [{"id": AIR_1}]}]}
        detail = {"code": 0, "data": {
            "missionBasicData": {"type": "Strike", "assignedCombatUnit": [{"key": AIR_1}]},
            "strikeMissionData": {"targetList": [{"id": target}]}}}
        _, targets, count = self._read_coverage(summary, detail)
        self.assertEqual(targets, {target})
        self.assertEqual(count, 2)

    def test_coverage_excludes_empty_and_navy_main_even_with_air_escort(self):
        summary = {"code": 0, "data": [{"id": TASK_1, "type": "Strike"}]}
        for main in ([], [{"key": SHIP}]):
            detail = {"code": 0, "data": {
                "missionBasicData": {"type": "Strike", "assignedCombatUnit": main,
                                     "assignedEscortUnit": [{"key": AIR_1}]},
                "strikeMissionData": {"targetList": [{"id": TASK_2}]}}}
            with self.subTest(main=main):
                assignments, targets, count = self._read_coverage(summary, detail)
                self.assertEqual(assignments, {})
                self.assertEqual(targets, set())
                self.assertEqual(count, 2)

    def test_coverage_does_not_read_detail_for_known_patrol_summary(self):
        summary = {"code": 0, "data": [{"id": TASK_1, "type": "Patrol",
                                           "assignUnit": [{"id": AIR_1}]}]}
        assignments, targets, count = self._read_coverage(summary, {})
        self.assertEqual(assignments, {AIR_1: TASK_1})
        self.assertEqual(targets, set())
        self.assertEqual(count, 1)

    def test_malformed_strike_targets_fail_without_partial_sink_update(self):
        summary = {"code": 0, "data": [{"id": TASK_1, "type": "Strike"}]}
        for strike in ({}, {"targetList": "bad"}, {"targetList": [{"id": "bad"}]}):
            detail = {"code": 0, "data": {
                "missionBasicData": {"type": "Strike", "assignedCombatUnit": [{"key": AIR_1}]},
                "strikeMissionData": strike}}
            responses = [summary, detail]
            sink = {TASK_2}
            with self.subTest(strike=strike), patch(
                    "runtime.mozi.mission_assignments.urlopen",
                    side_effect=lambda url, timeout: BytesIO(
                        json.dumps(responses.pop(0)).encode("utf-8"))):
                with self.assertRaises(RuntimeError):
                    read_blue_mission_assignments("http://127.0.0.1:8050/ApiCtrl/api",
                        SIDE, {AIR_1}, active_strike_target_guids=sink)
                self.assertEqual(sink, {TASK_2})

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
