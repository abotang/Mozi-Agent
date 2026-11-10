"""Run the fixed PowerShell 7 script for the blue-side opening ROE."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from runtime.execution.executor import find_powershell


def set_opening_engagement(situation: dict, script_path: str | Path,
                           powershell: str | None = None) -> dict:
    command = [find_powershell(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-File", str(script_path),
               "-ApiBase", situation["api_base"], "-SideGuid", situation["side"]["guid"]]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=45, check=False, shell=False)
    try:
        result = json.loads(completed.stdout.strip().lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"蓝方开局接战条令脚本未返回 JSON：{completed.stderr[:400]}") from exc
    if (completed.returncode != 0 or not isinstance(result, dict)
            or result.get("success") is not True or result.get("value") != "1"):
        raise RuntimeError(f"蓝方开局接战条令未确认：{result!r} {completed.stderr[:400]}")
    return result
