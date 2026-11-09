"""本地 Agent 调用审计；每条 JSONL 记录一次输入、输出或错误事件。"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
from uuid import uuid4


class AgentTraceWriter:
    def __init__(self, log_dir: str | None):
        self.path = Path(log_dir) / "events.jsonl" if log_dir else None
        self._lock = threading.Lock()
        self._warned = False
        self.session_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}-{uuid4().hex[:6]}"
        self._scope = {}

    def set_scope(self, **values) -> None:
        with self._lock:
            self._scope = {key: value for key, value in values.items() if value is not None}

    def write(self, event: dict) -> None:
        if self.path is None:
            return
        try:
            with self._lock:
                record = {"timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                          "session_id": self.session_id, **self._scope, **event}
                line = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str) + "\n"
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(line)
        except (OSError, TypeError, ValueError) as exc:
            # 监控盘不可写不应中断墨子任务调度；只告警一次。
            if not self._warned:
                print(f"Agent 监控日志写入失败：{exc}", file=sys.stderr, flush=True)
                self._warned = True
