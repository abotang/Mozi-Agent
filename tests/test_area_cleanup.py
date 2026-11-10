import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.execution.area_cleanup import OwnedAreaGarbageCollector


SIDE = "11111111-1111-1111-1111-111111111111"
ORPHAN = "22222222-2222-2222-2222-222222222222"
LINKED = "33333333-3333-3333-3333-333333333333"
MANUAL = "44444444-4444-4444-4444-444444444444"
REF = "55555555-5555-5555-5555-555555555555"


class AreaCleanupTests(unittest.TestCase):
    def test_updated_patrol_deletes_old_area_despite_stale_mission_list(self):
        mission = "77777777-7777-7777-7777-777777777777"
        old_refs = ["55555555-5555-5555-5555-555555555551",
                    "55555555-5555-5555-5555-555555555552",
                    "55555555-5555-5555-5555-555555555553"]
        new_refs = ["66666666-6666-6666-6666-666666666661",
                    "66666666-6666-6666-6666-666666666662",
                    "66666666-6666-6666-6666-666666666663"]
        with TemporaryDirectory(dir=ROOT) as folder:
            for index, (area_guid, refs) in enumerate(((ORPHAN, old_refs), (LINKED, new_refs))):
                report = Path(folder) / f"run-{index}" / "reports" / "task.json"
                report.parent.mkdir(parents=True)
                report.write_text(json.dumps({"side": {"guid": SIDE}, "tasks": [{
                    "missionGuid": mission, "area": {"guid": area_guid, "reused": False,
                                                     "reference": {"data": refs}}}]}), encoding="utf-8")
                os.utime(report, ns=(1_000_000_000 * (index + 1),
                                     1_000_000_000 * (index + 1)))
            areas = {
                ORPHAN: {"id": ORPHAN, "missionList": [{"id": mission}],
                         "points": [{"id": guid} for guid in old_refs]},
                LINKED: {"id": LINKED, "missionList": [{"id": mission}],
                         "points": [{"id": guid} for guid in new_refs]},
            }

            def get_json(url):
                if "/Mission?" in url:
                    return {"code": 0, "data": {"patrolmissionData": {
                        "PatrolArea_ID": LINKED,
                        "PatrolArea": [{"id": guid} for guid in new_refs],
                        "AlertArea": [{"id": guid} for guid in new_refs]}}}
                return {"code": 0, "data": {"sideAreas": [{"guid": SIDE,
                    "areaTypes": [{"areas": list(areas.values())}]}]}}

            def post_json(url, body):
                if url.endswith("/ScenArea"):
                    areas.pop(str(body["area_id"]).strip("{}"))
                return {"code": 0}

            result = OwnedAreaGarbageCollector(folder, get_json=get_json,
                                               post_json=post_json).collect({
                "api_base": "http://example/api", "side": {"guid": SIDE, "name": "蓝方"}})
            self.assertEqual(result["deleted"], [ORPHAN])
            self.assertEqual(result["stale_links"], [ORPHAN])
            self.assertEqual(result["linked"], [LINKED])
            self.assertNotIn(ORPHAN, areas)
            self.assertIn(LINKED, areas)

    def test_deletes_only_owned_unlinked_area_and_its_exclusive_reference(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            reports = Path(folder) / "run-1" / "reports"
            reports.mkdir(parents=True)
            (reports / "one.json").write_text(json.dumps({
                "side": {"guid": SIDE},
                "tasks": [{"name": "原巡逻任务-换区前", "missionGuid": "77777777-7777-7777-7777-777777777777",
                           "area": {"guid": ORPHAN, "reused": False,
                                    "reference": {"data": [REF]}}},
                          {"name": "原巡逻任务-换区后", "missionGuid": "77777777-7777-7777-7777-777777777777",
                           "area": {"guid": LINKED, "reused": False}}],
            }), encoding="utf-8")
            areas = {
                ORPHAN: {"id": "{" + ORPHAN + "}", "missionList": [],
                         "points": [{"id": REF}]},
                LINKED: {"id": "{" + LINKED + "}",
                         "missionList": [{"id": "77777777-7777-7777-7777-777777777777"}],
                         "points": []},
                MANUAL: {"id": "{" + MANUAL + "}", "missionList": [], "points": []},
            }
            posts = []

            def get_json(_url):
                return {"code": 0, "data": {"sideAreas": [{
                    "guid": SIDE, "areaTypes": [{"areas": list(areas.values())}]
                }]}}

            def post_json(url, body):
                posts.append((url, body))
                if url.endswith("/ScenArea"):
                    areas.pop(ORPHAN)
                return {"code": 0, "data": None}

            cleaner = OwnedAreaGarbageCollector(folder, get_json=get_json,
                                                 post_json=post_json)
            result = cleaner.collect({"api_base": "http://example/api",
                                      "side": {"guid": SIDE, "name": "蓝方"}})
            self.assertEqual(result["deleted"], [ORPHAN])
            self.assertEqual(result["linked"], [LINKED])
            self.assertIn(LINKED, areas)
            self.assertIn(MANUAL, areas)
            self.assertEqual([body["option"] for url, body in posts
                              if url.endswith("/ScenArea")], ["delArea"])
            self.assertEqual([body["Values"] for url, body in posts
                              if url.endswith("/ReferencePoint")], [[{"Guid": REF}]])

    def test_journal_only_area_with_missing_links_is_preserved(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            reports = Path(folder) / "run-1" / "reports"
            reports.mkdir(parents=True)
            (reports / "one.json.area.json").write_text(json.dumps({
                "created": True, "sideGuid": SIDE, "areaGuid": ORPHAN,
                "referenceGuids": [REF],
            }), encoding="utf-8")
            cleaner = OwnedAreaGarbageCollector(
                folder,
                get_json=lambda _: {"code": 0, "data": {"sideAreas": [{
                    "guid": SIDE, "areaTypes": [{"areas": [{"id": ORPHAN}]}]
                }]}},
                post_json=lambda *_: self.fail("must not delete without missionList"))
            result = cleaner.collect({"api_base": "http://example/api",
                                      "side": {"guid": SIDE, "name": "蓝方"}})
            self.assertEqual(result["deleted"], [])
            self.assertIn("缺少可核对的 missionList", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
