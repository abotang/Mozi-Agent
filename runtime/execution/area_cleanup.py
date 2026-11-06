"""只清理本项目创建且没有任何任务关联的墨子区域。"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID


def _guid(value) -> str:
    try:
        return str(UUID(str(value).strip().strip("{}")))
    except (ValueError, TypeError, AttributeError):
        return ""


def _get(url: str):
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def _post(url: str, body: dict):
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"},
                      method="POST")
    with urlopen(request, timeout=15) as response:
        return json.load(response)


class OwnedAreaGarbageCollector:
    def __init__(self, run_root: str | Path, *, get_json=_get, post_json=_post):
        self.run_root = Path(run_root)
        self.get_json = get_json
        self.post_json = post_json

    def _owned(self, side_guid: str) -> dict[str, set[str]]:
        owned: dict[str, set[str]] = {}
        for path in self.run_root.glob("*/reports/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if path.name.endswith(".area.json"):
                if (_guid(payload.get("sideGuid")) != side_guid
                        or payload.get("created") is not True):
                    continue
                area_guid = _guid(payload.get("areaGuid"))
                refs = payload.get("referenceGuids")
                if area_guid and isinstance(refs, list):
                    owned.setdefault(area_guid, set()).update(
                        ref for value in refs if (ref := _guid(value)))
                continue
            side = payload.get("side")
            if not isinstance(side, dict) or _guid(side.get("guid")) != side_guid:
                continue
            tasks = payload.get("tasks")
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                area = task.get("area")
                if not isinstance(area, dict) or area.get("reused") is not False:
                    continue
                area_guid = _guid(area.get("guid"))
                if not area_guid:
                    continue
                reference = area.get("reference")
                refs = reference.get("data") if isinstance(reference, dict) else None
                if isinstance(refs, list):
                    owned.setdefault(area_guid, set()).update(
                        ref for value in refs if (ref := _guid(value)))
                else:
                    owned.setdefault(area_guid, set())
        return owned

    @staticmethod
    def _areas(payload: dict, side_guid: str) -> dict[str, dict]:
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise RuntimeError("ScenArea 区域列表读取失败")
        data = payload.get("data")
        sides = data.get("sideAreas") if isinstance(data, dict) else None
        if not isinstance(sides, list):
            raise RuntimeError("ScenArea 未返回可核对的 sideAreas")
        result = {}
        for side in sides:
            if not isinstance(side, dict) or _guid(side.get("guid")) != side_guid:
                continue
            for group in side.get("areaTypes", []):
                if not isinstance(group, dict):
                    continue
                for area in group.get("areas", []):
                    if isinstance(area, dict) and (key := _guid(area.get("id"))):
                        result[key] = area
        return result

    def collect(self, situation: dict) -> dict:
        side_guid = _guid(situation["side"]["guid"])
        owned = self._owned(side_guid)
        result = {"owned": len(owned), "deleted": [], "linked": [], "errors": []}
        if not owned:
            return result
        base = situation["api_base"].rstrip("/")
        areas = self._areas(self.get_json(f"{base}/ScenArea?type=all"), side_guid)
        for area_guid, references in owned.items():
            area = areas.get(area_guid)
            if area is None:
                continue
            links = area.get("missionList")
            if not isinstance(links, list):
                result["errors"].append(f"{area_guid}: 缺少可核对的 missionList")
                continue
            if links:
                result["linked"].append(area_guid)
                continue
            response = self.post_json(f"{base}/ScenArea", {
                "option": "delArea", "side": situation["side"]["name"],
                "area_id": area["id"],
            })
            if not isinstance(response, dict) or response.get("code") != 0:
                result["errors"].append(f"{area_guid}: 删除区域失败")
                continue
            areas = self._areas(self.get_json(f"{base}/ScenArea?type=all"), side_guid)
            if area_guid in areas:
                result["errors"].append(f"{area_guid}: 删除后仍可查到区域")
                continue
            result["deleted"].append(area_guid)
            area_refs = {_guid(point.get("id")) for point in area.get("points", [])
                         if isinstance(point, dict)}
            other_refs = {_guid(point.get("id")) for other in areas.values()
                          for point in other.get("points", []) if isinstance(point, dict)}
            removable = sorted((references & area_refs) - other_refs)
            if removable:
                response = self.post_json(f"{base}/ReferencePoint", {
                    "Type": "RemoveReferencePoint", "SideGuid": side_guid,
                    "Values": [{"Guid": value} for value in removable],
                })
                if not isinstance(response, dict) or response.get("code") != 0:
                    result["errors"].append(f"{area_guid}: 区域已删除，但参考点清理失败")
        return result
