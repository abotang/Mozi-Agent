"""只读本地 HTTP 服务：把持久 Agent JSONL 日志展示给浏览器。"""

from __future__ import annotations

import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from agents.model_client import AgentConfig


ROOT = Path(__file__).resolve().parents[1]
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


class EventLogReader:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._index_lock = threading.Lock()
        self._index_cursor = 0
        self._groups: dict[str, dict] = {}

    @staticmethod
    def _decode(raw: bytes) -> dict | None:
        if not raw.endswith(b"\n"):
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            return None
        return value if isinstance(value, dict) else None

    def _records(self, *, start: int = 0, end: int | None = None):
        if not self.path.is_file():
            return
        with self.path.open("rb") as stream:
            size = self.path.stat().st_size
            stream.seek(min(max(start, 0), size))
            boundary = min(end, size) if end is not None else size
            while stream.tell() < boundary:
                offset = stream.tell()
                raw = stream.readline()
                if not raw or stream.tell() > boundary:
                    break
                event = self._decode(raw)
                if raw.endswith(b"\n"):
                    yield offset, stream.tell(), event

    def sessions(self) -> dict:
        with self._index_lock:
            size = self.path.stat().st_size if self.path.is_file() else 0
            if size < self._index_cursor:
                self._index_cursor = 0
                self._groups = {}
            for _, next_offset, event in self._records(start=self._index_cursor):
                self._index_cursor = next_offset
                if event is None:
                    continue
                session_id = str(event.get("session_id") or "legacy")
                row = self._groups.setdefault(session_id, {
                    "id": session_id, "first": event.get("timestamp", ""),
                    "last": "", "scenario_name": "", "scenario_key": "",
                    "events": 0, "calls": 0, "errors": 0, "skips": 0,
                })
                row["last"] = event.get("timestamp", "")
                row["events"] += 1
                row["calls"] += event.get("event") == "input"
                row["errors"] += event.get("event") == "error"
                row["skips"] += event.get("event") == "skip"
                row["scenario_name"] = event.get("scenario_name") or row["scenario_name"]
                row["scenario_key"] = event.get("scenario_key") or row["scenario_key"]
            return {"sessions": sorted((dict(row) for row in self._groups.values()),
                                       key=lambda item: item["last"], reverse=True),
                    "log_bytes": size}

    def history(self, *, session: str = "", limit: int = 100, before: int | None = None) -> dict:
        size = self.path.stat().st_size if self.path.is_file() else 0
        boundary = min(max(before if before is not None else size, 0), size)
        recent = deque(maxlen=limit)
        matching = 0
        complete_end = 0
        for offset, next_offset, event in self._records(end=boundary):
            complete_end = next_offset
            if event is None:
                continue
            if session and str(event.get("session_id") or "legacy") != session:
                continue
            recent.append({"offset": offset, "event": event})
            matching += 1
        items = list(recent)
        return {"events": items, "start_cursor": items[0]["offset"] if items else 0,
                "end_cursor": complete_end, "has_more": matching > len(items)}

    def since(self, *, after: int, session: str = "", limit: int = 100) -> dict:
        size = self.path.stat().st_size if self.path.is_file() else 0
        start = min(max(after, 0), size)
        cursor = start
        items = []
        for offset, next_offset, event in self._records(start=start):
            cursor = next_offset
            if event is None:
                continue
            if session and str(event.get("session_id") or "legacy") != session:
                continue
            items.append({"offset": offset, "event": event})
            if len(items) >= limit:
                break
        return {"events": items, "next_cursor": cursor, "end_cursor": size}

    def export_lines(self, session: str):
        for _, _, event in self._records():
            if event is None:
                continue
            if str(event.get("session_id") or "legacy") == session:
                yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


def handler_factory(reader: EventLogReader):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status=200):
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/api/sessions":
                self._json(reader.sessions())
                return
            if url.path == "/api/events":
                query = parse_qs(url.query)
                session = query.get("session", [""])[0]
                try:
                    limit = min(max(int(query.get("limit", ["100"])[0]), 1), 500)
                    if "after" in query:
                        result = reader.since(after=int(query["after"][0]),
                                              session=session, limit=limit)
                    else:
                        before = int(query["before"][0]) if "before" in query else None
                        result = reader.history(session=session, limit=limit, before=before)
                except ValueError:
                    self._json({"error": "游标和条数必须是整数"}, 400)
                    return
                self._json(result)
                return
            if url.path == "/api/export":
                session = parse_qs(url.query).get("session", [""])[0]
                if not session:
                    self._json({"error": "请选择推演场次"}, 400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="mozi-agent-events.jsonl"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                for line in reader.export_lines(session):
                    self.wfile.write(line)
                return
            asset = ASSETS.get(url.path)
            if asset:
                path = Path(__file__).parent / asset[0]
                self._send(200, path.read_bytes(), asset[1])
                return
            self._json({"error": "未找到页面"}, 404)

        def log_message(self, format, *args):
            # 高频轮询不刷屏；启动信息由 main 打印。
            return

    return Handler


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本地 Agent 输入输出监控网页")
    parser.add_argument("--llm-config", default=str(ROOT / "config" / "llm.json"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0～65535")
    config = AgentConfig.load(args.llm_config)
    if not config.log_dir:
        parser.error("模型配置缺少 log_dir")
    reader = EventLogReader(Path(config.log_dir) / "events.jsonl")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory(reader))
    address = f"http://127.0.0.1:{server.server_port}/"
    print(f"Agent 监控前端：{address}", flush=True)
    print(f"历史日志：{reader.path}", flush=True)
    print("按 Ctrl+C 停止网页服务；推演程序不受影响。", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
