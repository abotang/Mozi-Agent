"""只清理本项目创建且没有任务实际使用的墨子区域。"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote
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

    def _owned(self, side_guid: str) -> tuple[dict[str, set[str]], dict[str, str], dict[str, list[tuple[str, str]]]]:
        owned: dict[str, set[str]] = {}
        # Patrol and alert areas are separate descendants of the same mission.
        # Track each slot separately so its old area can be collected after an
        # update without mistaking the other slot's current area for a replacement.
        latest_area: dict[tuple[str, str], tuple[int, str, str]] = {}
        mission_areas: dict[tuple[str, str], set[str]] = {}
        for path in self.run_root.glob("*/reports/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            journal_suffix = path.name.rpartition(".area.")[2]
            is_area_journal = (path.name.endswith(".area.json") or
                               (journal_suffix.endswith(".json") and
                                bool(_guid(journal_suffix[:-5]))))
            if is_area_journal:
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
                mission_guid = _guid(task.get("missionGuid"))
                for slot, key in (("patrol", "area"), ("alert", "alertArea")):
                    area = task.get(key)
                    if not isinstance(area, dict) or area.get("reused") is not False:
                        continue
                    area_guid = _guid(area.get("guid"))
                    if not area_guid:
                        continue
                    if mission_guid:
                        lineage = (mission_guid, slot)
                        mission_areas.setdefault(lineage, set()).add(area_guid)
                        marker = (path.stat().st_mtime_ns, str(path), area_guid)
                        if marker > latest_area.get(lineage, (-1, "", "")):
                            latest_area[lineage] = marker
                    reference = area.get("reference")
                    refs = reference.get("data") if isinstance(reference, dict) else None
                    if isinstance(refs, list):
                        owned.setdefault(area_guid, set()).update(
                            ref for value in refs if (ref := _guid(value)))
                    else:
                        owned.setdefault(area_guid, set())
        # A stale area may remain in ScenArea.missionList after the mission
        # switches to a new patrol or alert area. Verify against Mission detail.
        previous = {area: lineage[1] for lineage, areas in mission_areas.items()
                    if len(areas) > 1 for area in areas
                    if area != latest_area[lineage][2]}
        current: dict[str, list[tuple[str, str]]] = {}
        for (mission_guid, slot), (_, _, area_guid) in latest_area.items():
            current.setdefault(area_guid, []).append((mission_guid, slot))
        return owned, previous, current

    def _stale_patrol_links(self, base: str, area_guid: str, area: dict,
                            links: list, slot: str) -> bool:
        """Confirm every listed task now uses a different patrol/alert area."""
        old_points = {_guid(point.get("id")) for point in (area.get("points") or [])
                      if isinstance(point, dict)}
        if len(old_points) < 3:
            return False
        for link in links:
            mission_guid = _guid(link.get("id")) if isinstance(link, dict) else ""
            if not mission_guid:
                return False
            try:
                detail = self.get_json(f"{base}/Mission?Guid={quote(mission_guid)}")
            except Exception:
                return False
            data = detail.get("data") if isinstance(detail, dict) and detail.get("code") == 0 else None
            patrol = data.get("patrolmissionData") if isinstance(data, dict) else None
            id_field = "AlertArea_ID" if slot == "alert" else "PatrolArea_ID"
            if not isinstance(patrol, dict) or not _guid(patrol.get(id_field)):
                return False
            if _guid(patrol[id_field]) == area_guid:
                return False
            current_points = set()
            for key in ("PatrolArea", "AlertArea"):
                rows = patrol.get(key)
                if not isinstance(rows, list):
                    return False
                current_points.update(_guid(point.get("id")) for point in rows
                                      if isinstance(point, dict))
            if old_points.intersection(current_points):
                return False
        return True

    def _current_area_usage(self, base: str, area_guid: str, area: dict,
                            missions: list[tuple[str, str]]) -> bool | None:
        """Check report-linked current areas even when ScenArea omits links.

        ``True`` means a mission uses the area, ``False`` means no listed
        mission does, and ``None`` means a failed or ambiguous read. Keeping an
        ambiguous area is safer than deleting a live alert region.
        """
        old_points = {_guid(point.get("id")) for point in (area.get("points") or [])
                      if isinstance(point, dict)}
        for mission_guid, slot in missions:
            try:
                detail = self.get_json(f"{base}/Mission?Guid={quote(mission_guid)}")
            except Exception:
                return None
            if not isinstance(detail, dict):
                return None
            if detail.get("code") == 101 and detail.get("message") == "任务不存在":
                continue
            data = detail.get("data") if detail.get("code") == 0 else None
            patrol = data.get("patrolmissionData") if isinstance(data, dict) else None
            if not isinstance(patrol, dict):
                return None
            id_field = "AlertArea_ID" if slot == "alert" else "PatrolArea_ID"
            current_id = _guid(patrol.get(id_field))
            if current_id:
                if current_id == area_guid:
                    return True
                continue
            # Some Mozi builds omit AlertArea_ID yet return its reference
            # points. Never delete when those points still overlap this area.
            points_key = "AlertArea" if slot == "alert" else "PatrolArea"
            rows = patrol.get(points_key)
            if not isinstance(rows, list) or not old_points:
                return None
            current_points = {_guid(point.get("id")) for point in rows
                              if isinstance(point, dict)}
            if old_points.intersection(current_points):
                return True
        return False

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
        owned, previous, current = self._owned(side_guid)
        result = {"owned": len(owned), "deleted": [], "linked": [],
                  "stale_links": [], "errors": []}
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
                if (area_guid not in previous
                        or not self._stale_patrol_links(base, area_guid, area, links,
                                                        previous[area_guid])):
                    result["linked"].append(area_guid)
                    continue
                result["stale_links"].append(area_guid)
            elif area_guid in current:
                # Alert areas may lack missionList even while Mission detail
                # still references them; report order alone is not authority.
                usage = self._current_area_usage(base, area_guid, area,
                                                  current[area_guid])
                if usage is True:
                    result["linked"].append(area_guid)
                    continue
                if usage is None:
                    result["errors"].append(
                        f"{area_guid}: 当前任务关联不可核对；保留区域")
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
