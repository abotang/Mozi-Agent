"""Read the blue mission catalog before resolving an uncertain task result."""

from __future__ import annotations

import json
from urllib.parse import quote
from urllib.request import urlopen
from uuid import UUID


def read_blue_missions(api_base: str, side_guid: str) -> list[dict[str, str]]:
    side = str(UUID(str(side_guid).strip().strip("{}")))
    base = str(api_base).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("墨子 API 地址必须是 HTTP(S)")
    with urlopen(f"{base}/MissionGant?SideGuid={quote(side)}", timeout=12) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError("墨子未成功返回蓝方任务列表")
    rows = payload.get("data")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RuntimeError("蓝方任务列表格式未知")
    missions = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
            raise RuntimeError("蓝方任务列表缺少任务 GUID 或名称")
        missions.append({"guid": str(UUID(str(row["id"]).strip().strip("{}"))),
                         "name": str(row["name"]), "type": str(row.get("type") or "")})
    return missions
