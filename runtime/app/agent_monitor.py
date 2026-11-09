"""独立终端查看五个 Agent 的本地输入/输出事件。"""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import sys
import time

from agents.model_client import AgentConfig


NAMES = {
    "joint": "联合指挥", "component_air": "空军", "component_awacs": "预警机",
    "component_navy": "海军", "component_army": "陆军",
}


def _decode(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _show(event: dict, summary: bool) -> None:
    agent = str(event.get("agent", "?"))
    kind = str(event.get("event", "?"))
    header = (f"\n[{event.get('timestamp', '?')}] {NAMES.get(agent, agent)} "
              f"{kind}  调用 {str(event.get('call_id', '-'))[:8]}")
    if event.get("duration_sec") is not None:
        header += f"  {event['duration_sec']} 秒"
    print(header, flush=True)
    if summary:
        details = {key: event[key] for key in ("model", "reason", "duration_sec", "usage", "error")
                   if key in event}
        if kind == "input":
            context = event.get("input", {}).get("context", {})
            details["input_sections"] = list(context) if isinstance(context, dict) else []
        elif kind in ("output", "cache", "skip"):
            value = event.get("output", {})
            if isinstance(value, dict):
                details["summary"] = value.get("summary", "")
                details["skill_calls"] = len(value.get("skill_calls") or [])
        print(json.dumps(details, ensure_ascii=False, indent=2, default=str), flush=True)
    else:
        details = {key: value for key, value in event.items()
                   if key not in ("timestamp", "agent", "event", "call_id")}
        print(json.dumps(details, ensure_ascii=False, indent=2, default=str), flush=True)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="实时监控联合指挥、空军、预警机、海军、陆军 Agent 输入输出")
    parser.add_argument("--llm-config", default=str(root / "config" / "llm.json"))
    parser.add_argument("--agent", choices=tuple(NAMES), help="只看指定 Agent")
    parser.add_argument("--last", type=int, default=10, help="启动时回看最近几条；默认 10")
    parser.add_argument("--once", action="store_true", help="只显示已有记录后退出")
    parser.add_argument("--summary", action="store_true", help="只显示摘要，不展开完整输入输出")
    args = parser.parse_args(argv)
    if args.last < 0:
        parser.error("--last 不能小于 0")
    config = AgentConfig.load(args.llm_config)
    if not config.log_dir:
        parser.error("模型配置缺少 log_dir，无法确定 Agent 监控日志位置")
    path = Path(config.log_dir) / "events.jsonl"
    print(f"Agent 监控日志：{path}", flush=True)
    recent = deque(maxlen=args.last) if args.last else None
    position = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as stream:
            if recent is None:
                stream.seek(0, 2)
            else:
                for line in stream:
                    event = _decode(line)
                    if event and (not args.agent or event.get("agent") == args.agent):
                        recent.append(event)
            position = stream.tell()
        if recent is not None:
            for event in recent:
                _show(event, args.summary)
    if args.once:
        return 0
    print("正在等待新事件；按 Ctrl+C 退出。", flush=True)
    try:
        while True:
            if not path.exists():
                time.sleep(0.3)
                continue
            size = path.stat().st_size
            if size < position:
                position = 0
            with path.open("r", encoding="utf-8") as stream:
                stream.seek(position)
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        break
                    position = stream.tell()
                    event = _decode(line)
                    if event and (not args.agent or event.get("agent") == args.agent):
                        _show(event, args.summary)
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n监控已停止。", flush=True)
        return 0
