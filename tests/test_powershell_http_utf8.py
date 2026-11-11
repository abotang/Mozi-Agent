import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.skills.compiler import PlanCompiler
from runtime.execution.executor import PowerShellMissionExecutor, ScriptExecutionError
from runtime.skills.registry import SkillRegistry
from runtime.planning.validation import validate_plan


class PowerShellUtf8Tests(unittest.TestCase):
    def test_powershell_7_posts_chinese_side_name_as_utf8(self):
        executable = shutil.which("pwsh")
        if not executable:
            self.skipTest("PowerShell 7 unavailable")

        class Handler(BaseHTTPRequestHandler):
            requests = []

            def log_message(self, *_):
                pass

            def respond(self, data):
                body = json.dumps({"code": 0, "data": data, "message": "succeed"},
                                  ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw.decode("utf-8"))
                self.requests.append((self.path, self.headers.get("Content-Type"), body))
                if self.path.endswith("/ReferencePoint"):
                    self.respond([f"10000000-0000-0000-0000-{index:012d}"
                                  for index, _ in enumerate(body["Values"], 1)])
                elif self.path.endswith("/ScenArea"):
                    self.respond({"id": "20000000-0000-0000-0000-000000000001"})
                elif body.get("type") == "createMission":
                    self.respond({"id": "30000000-0000-0000-0000-000000000001"})
                else:
                    if body.get("type") == "PatrolMissionRules":
                        self.server.last_otr = body["Rules"]["OTR"]
                        self.server.last_patrol_rules = body["Rules"]
                    if body.get("type") == "SupportMissionRules":
                        self.server.last_support_alt = body["Rules"]["StationAltitude_Aircraft"]
                        self.server.last_support_transit = body["Rules"]["TransitAltitude_Aircraft"]
                        self.server.last_support_emcon = body["Rules"]["AEOOS"]
                        self.server.last_support_one_tanking = body["Rules"]["OneTankingCycleOnly"]
                    if self.path.endswith("/DoctrineInfo"):
                        self.server.last_mission_emcon = body["Value"]["EMCSetting"]
                    self.respond({})

            def do_GET(self):
                if self.path.startswith("/ApiCtrl/api/DoctrineInfo?"):
                    self.respond({"EMCSetting": getattr(
                        self.server, "mission_emcon_override",
                        getattr(self.server, "last_mission_emcon", {}))})
                    return
                self.respond({"patrolmissionData": {
                    "PatrolArea": [1, 2, 3], "OTR": getattr(self.server, "last_otr", False),
                    "TransitAltitude_Aircraft": getattr(
                        self.server, "last_patrol_rules", {}).get(
                            "TransitAltitude_Aircraft", "未定"),
                    "StationAltitude_Aircraft": getattr(
                        self.server, "last_patrol_rules", {}).get(
                            "StationAltitude_Aircraft", "未定")},
                    "supportmissionData": {
                        "area": [1, 2, 3],
                        "StationAltitude_Aircraft": getattr(
                            self.server, "support_alt_override",
                            getattr(self.server, "last_support_alt", "未定")),
                        "TransitAltitude_Aircraft": getattr(
                            self.server, "support_transit_override",
                            getattr(self.server, "last_support_transit", "未定")),
                        "AEOOS": getattr(self.server, "support_emcon_override",
                                          getattr(self.server, "last_support_emcon", False)),
                        "OneTankingCycleOnly": getattr(
                            self.server, "support_one_tanking_override",
                            getattr(self.server, "last_support_one_tanking", False))}})

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("Loopback listener unavailable")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            registry = SkillRegistry(ROOT / "skills").load()
            situation = json.loads((ROOT / "examples" / "situation.example.json")
                                   .read_text(encoding="utf-8"))
            situation["units"]["army"].append({
                "guid": "cccccccc-0000-0000-0000-000000000002",
                "name": "蓝方机场", "unit_type": "Facility", "lat": 20.3, "lon": 130.3})
            situation["api_base"] = f"http://127.0.0.1:{server.server_port}/ApiCtrl/api"
            source_plan = json.loads((ROOT / "examples" / "plan.multiple.example.json")
                                     .read_text(encoding="utf-8"))
            call = source_plan["skill_calls"][0]
            call["mission_name"] = "航母北侧战斗巡逻"
            call["area"]["name"] = "航母北侧战斗巡逻-区域"
            ship_call = json.loads(json.dumps(call))
            ship_call.update({
                "call_id": "ship-patrol",
                "skill_id": "navy.sea_patrol",
                "mission_name": "舰艇海上巡逻",
                "unit_guids": ["bbbbbbbb-0000-0000-0000-000000000001"],
            })
            awacs_call = json.loads(json.dumps(call))
            awacs_call.update({
                "call_id": "awacs-airfield", "skill_id": "air.early_warning",
                "mission_name": "机场近场预警",
                "unit_guids": ["aaaaaaaa-0000-0000-0000-000000000004"],
                "area": {"pattern": "racetrack"},
            })
            calls = validate_plan({"version": 1, "skill_calls": [call, ship_call, awacs_call]},
                                  situation, registry)
            with TemporaryDirectory(dir=ROOT) as folder:
                invocation = PlanCompiler(registry, ROOT / "scripts" / "powershell",
                                          folder).compile(calls, situation, "utf8")
                executor = PowerShellMissionExecutor(executable=executable)
                executor.execute(invocation, dry_run=True)
                results = [result for item in invocation
                           for result in executor.execute_prevalidated(item)]
                for item in invocation:
                    self.assertTrue(Path(str(item.report_path) + ".area.json").is_file())
                situation["units"]["air"].append({
                    "guid": "aaaaaaaa-0000-0000-0000-000000000005",
                    "name": "E-2K预警机-2", "unit_type": "Aircraft", "lat": 20.3, "lon": 130.3})
                mission_guid = "30000000-0000-0000-0000-000000000001"
                area_guid = "20000000-0000-0000-0000-000000000001"
                situation["existing_missions"] = [{
                    "name": "机场近场预警", "guid": mission_guid,
                    "skill_id": "air.early_warning", "unit_guids": awacs_call["unit_guids"],
                    "area_center": {"lat": 20.3, "lon": 130.3}}]
                situation["existing_areas"] = [{
                    "guid": area_guid, "name": "机场近场预警区", "type_id": "407",
                    "points": calls[-1].area["points"],
                    "mission_guids": [mission_guid],
                }]
                situation["operational_rules"] = {"awacs_trigger": {
                    "kind": "relief", "mission_name": "机场近场预警", "mission_guid": mission_guid,
                    "unit_guid": awacs_call["unit_guids"][0],
                    "replacement_guid": "aaaaaaaa-0000-0000-0000-000000000005",
                    "area_guid": area_guid, "area_points": calls[-1].area["points"],
                    "deployment": calls[-1].area["deployment"]}}
                relief = {"call_id": "awacs-relief", "skill_id": "air.early_warning",
                          "mission_name": "机场近场预警", "existing_mission_guid": mission_guid,
                          "unit_guids": ["aaaaaaaa-0000-0000-0000-000000000005"],
                          "area": {"pattern": "racetrack"},
                          "parameters": {"relieved_unit_guid": awacs_call["unit_guids"][0]}}
                relief_calls = validate_plan({"version": 1, "skill_calls": [relief]},
                                             situation, registry)
                relief_invocation = PlanCompiler(registry, ROOT / "scripts" / "powershell",
                                                 folder).compile(relief_calls, situation, "relief")
                executor.execute(relief_invocation, dry_run=True)
                executor.execute_prevalidated(relief_invocation[0])
                server.support_alt_override = "2"
                with self.assertRaisesRegex(ScriptExecutionError, "预警阵位高度回读不是10000米"):
                    executor.execute_prevalidated(relief_invocation[0])
                server.support_alt_override = "10000"
                server.support_transit_override = "2"
                with self.assertRaisesRegex(ScriptExecutionError, "预警出航高度回读不是2000米"):
                    executor.execute_prevalidated(relief_invocation[0])
                server.support_transit_override = "2000"
                server.support_emcon_override = False
                with self.assertRaisesRegex(ScriptExecutionError, "未回读到仅在阵位开启电磁辐射"):
                    executor.execute_prevalidated(relief_invocation[0])
                server.support_emcon_override = True
                server.support_one_tanking_override = False
                with self.assertRaisesRegex(ScriptExecutionError, "未回读到一个加油周期后返航选项"):
                    executor.execute_prevalidated(relief_invocation[0])
                server.support_one_tanking_override = True
                server.mission_emcon_override = {
                    "EMCON_AccordingSuperior": True, "Radar": "0", "OECM": "0", "Sonar": "0"}
                with self.assertRaisesRegex(ScriptExecutionError, "电磁管控未回读到"):
                    executor.execute_prevalidated(relief_invocation[0])
            self.assertTrue(results[-1].success, results[-1].stderr)
            area_posts = [item for item in Handler.requests if item[0].endswith("/ScenArea")]
            self.assertEqual(len(area_posts), 3)
            self.assertEqual(len(area_posts[-1][2]["area_points"]), 14)
            self.assertTrue(all(item[2]["side"] == "蓝方" for item in area_posts))
            self.assertTrue(all("charset=utf-8" in item[1].lower() for item in area_posts))
            mission_posts = [item for item in Handler.requests
                             if item[2].get("type") == "createMission"]
            self.assertEqual(mission_posts[0][2]["createMission"]["name"],
                             "航母北侧战斗巡逻")
            patrol_rules = [item[2]["Rules"] for item in Handler.requests
                            if item[2].get("type") == "PatrolMissionRules"]
            self.assertEqual([rule["OTR"] for rule in patrol_rules], [True, False])
            self.assertEqual(patrol_rules[0]["TransitAltitude_Aircraft"], "3000")
            self.assertEqual(patrol_rules[0]["StationAltitude_Aircraft"], "10000")
            self.assertNotIn("TransitAltitude_Aircraft", patrol_rules[1])
            self.assertNotIn("StationAltitude_Aircraft", patrol_rules[1])
            support_rules = [item[2]["Rules"] for item in Handler.requests
                             if item[2].get("type") == "SupportMissionRules"]
            self.assertEqual(len(support_rules), 7)
            self.assertTrue(support_rules[0]["OTR"])
            self.assertEqual([rule["StationAltitude_Aircraft"] for rule in support_rules],
                             ["10000"] * 7)
            self.assertEqual([rule["TransitAltitude_Aircraft"] for rule in support_rules],
                             ["2000"] * 7)
            self.assertTrue(all(rule["AEOIPA"] is True and rule["AEOOS"] is True
                                for rule in support_rules))
            self.assertTrue(all(rule["OneTankingCycleOnly"] is True
                                for rule in support_rules))
            doctrine_posts = [item[2] for item in Handler.requests
                             if item[0].endswith("/DoctrineInfo")]
            self.assertEqual(len(doctrine_posts), len(support_rules))
            self.assertTrue(all(body["Guid"] == mission_guid and
                                body["SubjectType"] == "Mission" and
                                body["IsEscort"] is False and
                                body["Value"]["EMCSetting"] == {
                                    "EMCON_AccordingSuperior": False,
                                    "Radar": "1", "OECM": "0", "Sonar": "0"}
                                for body in doctrine_posts))
            operations = [item[2]["type"] for item in Handler.requests
                          if item[2].get("type") in ("UnAssignUnitToMission", "AssignUnitToMission")]
            self.assertEqual(operations[-2:], ["UnAssignUnitToMission", "AssignUnitToMission"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
