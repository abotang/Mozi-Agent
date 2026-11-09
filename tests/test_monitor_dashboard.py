"""本地网页只读 API 的历史场次、分页和导出测试。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from monitor_web.server import EventLogReader


class DashboardLogTests(unittest.TestCase):
    def test_sessions_history_live_and_full_export(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            events = [
                {"timestamp": "2026-11-09T10:00:00", "session_id": "run-a", "agent": "joint", "event": "input", "scenario_name": "甲"},
                {"timestamp": "2026-11-09T10:00:01", "session_id": "run-a", "agent": "joint", "event": "output"},
                {"timestamp": "2026-11-09T11:00:00", "session_id": "run-b", "agent": "component_air", "event": "input", "scenario_name": "乙"},
                {"timestamp": "2026-11-09T11:00:01", "session_id": "run-b", "agent": "component_air", "event": "error"},
            ]
            path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
            reader = EventLogReader(path)
            sessions = reader.sessions()["sessions"]
            self.assertEqual([item["id"] for item in sessions], ["run-b", "run-a"])
            self.assertEqual(sessions[0]["errors"], 1)
            page = reader.history(session="run-a", limit=1)
            self.assertEqual(page["events"][0]["event"]["event"], "output")
            self.assertTrue(page["has_more"])
            older = reader.history(session="run-a", limit=1, before=page["start_cursor"])
            self.assertEqual(older["events"][0]["event"]["event"], "input")
            cursor = reader.history(session="run-b")["end_cursor"]
            with path.open("ab") as stream:
                stream.write((json.dumps({"timestamp": "2026-11-09T11:00:02", "session_id": "run-b",
                                          "agent": "component_air", "event": "skip"}) + "\n").encode())
            live = reader.since(after=cursor, session="run-b")
            self.assertEqual([item["event"]["event"] for item in live["events"]], ["skip"])
            self.assertEqual(reader.sessions()["sessions"][0]["skips"], 1)
            self.assertEqual(reader.sessions()["sessions"][0]["skips"], 1)
            exported = b"".join(reader.export_lines("run-a")).decode("utf-8")
            self.assertEqual(len(exported.splitlines()), 2)
            self.assertNotIn("run-b", exported)

    def test_incomplete_last_line_waits_for_completion(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            path.write_bytes(b'{"session_id":"run-a","event":"input"')
            reader = EventLogReader(path)
            self.assertEqual(reader.history()["end_cursor"], 0)
            self.assertEqual(reader.since(after=0)["next_cursor"], 0)
            with path.open("ab") as stream:
                stream.write(b'}\n')
            self.assertEqual(len(reader.since(after=0)["events"]), 1)


if __name__ == "__main__":
    unittest.main()
