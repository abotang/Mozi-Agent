"""Exercise the fixed jammer script against a local mock HTTP service."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "powershell" / "09-电子干扰伴随.ps1"
GUID = "22222222-2222-4222-8222-222222222222"


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"code": 0, "data": {
            "complyElectromagnetic": False,
            "sensorList": [{"type": "雷达"}, {"type": "ECM"}]}}).encode("utf-8"))

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.requests.append((self.path, self.headers.get("Content-Type"), json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"code":0,"data":true}')


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 not installed")
class JammerPowerShellTest(unittest.TestCase):
    def test_takeoff_and_follow_emitters_and_course(self):
        Handler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with TemporaryDirectory(dir=ROOT) as temp:
                path = Path(temp) / "config.json"
                base = {"schemaVersion": 1,
                        "apiBase": f"http://127.0.0.1:{server.server_port}",
                        "sideGuid": "44444444-4444-4444-8444-444444444444",
                        "missionGuid": "11111111-1111-4111-8111-111111111111",
                        "jammerGuid": GUID, "waypoint": None, "airborne": False}
                for mode in ("takeoff", "follow"):
                    config = {**base, "mode": mode,
                              "waypoint": ({"lat": 15.0, "lon": 142.2}
                                           if mode == "follow" else None)}
                    path.write_text(json.dumps(config), encoding="utf-8")
                    process = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive",
                                              "-File", str(SCRIPT), "-ConfigPath", str(path)],
                                             capture_output=True, text=True, encoding="utf-8",
                                             timeout=30, check=False)
                    self.assertEqual(0, process.returncode, process.stderr)
                    self.assertTrue(json.loads(process.stdout)["success"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        paths = [row[0] for row in Handler.requests]
        self.assertIn("/air-operations/aircraft-status", paths)
        self.assertIn("/PlottedCourse", paths)
        switches = [row[2].get("categorySwitch") for row in Handler.requests
                    if row[0] == "/unitSensors"]
        self.assertIn("RADER=true", switches)
        self.assertIn("OECM=true", switches)
        self.assertTrue(all("charset=utf-8" in row[1].lower() for row in Handler.requests))
        self.assertEqual([GUID], Handler.requests[0][2]["id"])


if __name__ == "__main__":
    unittest.main()
