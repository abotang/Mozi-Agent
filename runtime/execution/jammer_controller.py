"""Rule-driven jammer sorties paired with this project's aircraft strikes.

This controller does not ask an LLM to write code or rely on Mozi's unstable
escort label. It calls the fixed PowerShell script and verifies live flight
state on later polls. API acceptance is never reported as a completed sortie.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

from rules.jammer_support import (follow_waypoint, is_jammer,
                                  is_strike_record, package_center,
                                  should_follow, striker_aircraft)
from runtime.execution.executor import find_powershell


def _guid(value) -> str:
    return str(value or "").strip("{}").lower()


def _scenario_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("T", " "))
    except (TypeError, ValueError):
        return None


class JammerEscortController:
    def __init__(self, *, script_path: Path, run_root: Path,
                 powershell: str | None = None, trail_km: float = 20,
                 follow_interval_sec: float = 30, launch_retry_sec: float = 30,
                 max_wait_sim_sec: float = 1800,
                 clock=time.time, get_json=None, runner=subprocess.run):
        if not 0 < trail_km <= 100:
            raise ValueError("jammer_support.trail_km 必须在 0～100 km 之间")
        if min(follow_interval_sec, launch_retry_sec, max_wait_sim_sec) <= 0:
            raise ValueError("干扰机轮询间隔与等待上限必须大于 0")
        self.script_path = Path(script_path)
        self.run_root = Path(run_root)
        self.powershell = find_powershell(powershell)
        if not self.powershell or not self.script_path.is_file():
            raise RuntimeError("电子干扰伴随脚本或 PowerShell 7 不存在")
        self.trail_km = trail_km
        self.follow_interval_sec = follow_interval_sec
        self.launch_retry_sec = launch_retry_sec
        self.max_wait_sim_sec = max_wait_sim_sec
        self.clock = clock
        self.get_json = get_json or self._get_json
        self.runner = runner

    @staticmethod
    def _get_json(url: str) -> dict:
        with urlopen(url, timeout=12) as response:
            return json.load(response)

    def _trace(self, event: dict) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)
        with (self.run_root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                     **event}, ensure_ascii=False) + "\n")

    def _script(self, situation: dict, mission_guid: str, jammer_guid: str,
                mode: str, *, waypoint: dict | None = None, airborne: bool = False) -> dict:
        self.run_root.mkdir(parents=True, exist_ok=True)
        config = {
            "schemaVersion": 1, "apiBase": situation["api_base"],
            "sideGuid": situation["side"]["guid"], "missionGuid": mission_guid,
            "jammerGuid": jammer_guid, "mode": mode,
            "waypoint": waypoint, "airborne": airborne,
        }
        path = self.run_root / f"{_guid(mission_guid)}-{mode}-{uuid4().hex[:8]}.json"
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [self.powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(self.script_path),
                   "-ConfigPath", str(path)]
        completed = self.runner(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60,
                                check=False, shell=False)
        try:
            payload = json.loads(completed.stdout.strip().lstrip("\ufeff"))
        except (ValueError, TypeError):
            payload = None
        if completed.returncode != 0 or not isinstance(payload, dict) or payload.get("success") is not True:
            detail = (completed.stderr or completed.stdout or "脚本无有效 JSON 响应")[:1200]
            raise RuntimeError(f"干扰机 {mode} 失败：{detail}")
        self._trace({"event": mode, "mission_guid": mission_guid,
                     "jammer_guid": jammer_guid, "waypoint": waypoint,
                     "config": str(path)})
        return payload

    def _mission_rows(self, situation: dict) -> list[dict]:
        base, side = situation["api_base"].rstrip("/"), situation["side"]["guid"]
        result = self.get_json(f"{base}/MissionGant?SideGuid={side}")
        if result.get("code") != 0 or not isinstance(result.get("data"), list):
            raise RuntimeError("墨子任务列表未返回可核对的数据；不派出干扰机")
        return result["data"]

    def _mission_detail(self, situation: dict, mission_guid: str) -> dict | None:
        response = self.get_json(f"{situation['api_base'].rstrip('/')}/Mission?Guid={mission_guid}")
        if response.get("code") == 0 and isinstance(response.get("data"), dict):
            return response["data"]
        return None

    def _has_equipment(self, situation: dict, jammer_guid: str) -> bool:
        response = self.get_json(f"{situation['api_base'].rstrip('/')}/unitSensors?guid={jammer_guid}")
        if response.get("code") != 0 or not isinstance(response.get("data"), dict):
            return False
        labels = [str(row.get("type", "")) for row in response["data"].get("sensorList") or []]
        return (any("ECM" in label.upper() or "电子干扰" in label or "电子对抗" in label
                    for label in labels)
                and any("雷达" in label or "RADAR" in label.upper() for label in labels))

    @staticmethod
    def _occupied(rows: list[dict]) -> set[str]:
        occupied = set()
        for mission in rows:
            if not isinstance(mission, dict):
                continue
            for group in mission.get("assignUnit") or []:
                if not isinstance(group, dict):
                    continue
                children = group.get("children")
                units = children if isinstance(children, list) and children else [group]
                for unit in units:
                    if isinstance(unit, dict):
                        occupied.add(_guid(unit.get("id") or unit.get("key")))
        return occupied

    def _finish(self, situation: dict, mission_guid: str, record: dict,
                jammer: dict | None) -> str:
        if jammer is not None and jammer.get("airborne") is True and jammer.get("in_facility") is not True:
            self._script(situation, mission_guid, record["jammer_guid"], "finish", airborne=True)
        elif jammer is not None:
            # Even an aircraft back at base may still have emitters commanded on.
            self._script(situation, mission_guid, record["jammer_guid"], "finish")
        return f"干扰机 {record['jammer_guid']} 结束伴随 {mission_guid}"

    def tick(self, situation: dict, state: dict) -> list[str]:
        """Wait for observed main-aircraft takeoff, then escort the moving package."""
        bindings = state.setdefault("jammer_support", {})
        records = {str(row.get("guid")): row for row in state.get("missions", {}).values()
                   if isinstance(row, dict) and is_strike_record(row)}
        if not records and not bindings:
            return []
        now = self.clock()
        aircraft = situation["units"].get("air", [])
        by_guid = {_guid(unit.get("guid")): unit for unit in aircraft}
        rows = self._mission_rows(situation)
        occupied = self._occupied(rows)
        reserved = {_guid(row.get("jammer_guid")) for row in bindings.values()}
        messages = []
        current_sim = _scenario_time(situation["scenario"].get("start_time"))
        end_sim = _scenario_time(situation["scenario"].get("end_time"))

        for mission_guid, record in list(bindings.items()):
            if mission_guid not in records:
                jammer = by_guid.get(_guid(record.get("jammer_guid")))
                try:
                    messages.append(self._finish(situation, mission_guid, record, jammer))
                    del bindings[mission_guid]
                except Exception as exc:
                    messages.append(f"结束干扰伴随失败：{exc}")

        for mission_guid in sorted(records):
            detail = self._mission_detail(situation, mission_guid)
            strikers = striker_aircraft(detail or {}, aircraft)
            package = package_center(strikers)
            record = bindings.get(mission_guid)
            if not strikers or package is None:
                if record and record.get("phase") != "finished":
                    record["missing_count"] = int(record.get("missing_count", 0)) + 1
                    if record["missing_count"] >= 2:
                        jammer = by_guid.get(_guid(record.get("jammer_guid")))
                        try:
                            messages.append(self._finish(situation, mission_guid, record, jammer))
                            record["phase"] = "finished"
                        except Exception as exc:
                            messages.append(f"干扰机结束伴随失败：{exc}")
                continue
            if record:
                record.pop("missing_count", None)
            if record and record.get("phase") == "finished":
                # A prior solo sortie may have been recalled before the
                # striker ever launched. Rearm only after the main aircraft is
                # actually airborne and the previous jammer is confirmed home.
                old_jammer_guid = _guid(record.get("jammer_guid"))
                old_jammer = by_guid.get(old_jammer_guid)
                if (not package["airborne"] or old_jammer is None
                        or old_jammer.get("in_facility") is not True
                        or old_jammer.get("airborne") is True):
                    continue
                del bindings[mission_guid]
                reserved.discard(old_jammer_guid)
                record = None
            if record is None:
                # A Mission assignment only reserves a striker. It does not
                # prove the striker has launched; never send a jammer alone.
                if not package["airborne"]:
                    continue
                if current_sim and end_sim and current_sim >= end_sim:
                    continue
                candidates = [unit for unit in aircraft if is_jammer(unit)
                              and unit.get("in_facility") is True
                              and _guid(unit.get("guid")) not in occupied | reserved]
                candidates.sort(key=lambda unit: str(unit.get("guid", "")))
                jammer = next((unit for unit in candidates
                               if self._has_equipment(situation, str(unit["guid"]))), None)
                if jammer is None:
                    continue
                record = {"jammer_guid": jammer["guid"], "phase": "launch_requested",
                          "created_at_sim": situation["scenario"].get("start_time"),
                          "launch_origin": [package["lat"], package["lon"]],
                          "last_launch_at": now}
                bindings[mission_guid] = record
                reserved.add(_guid(jammer["guid"]))
                try:
                    self._script(situation, mission_guid, jammer["guid"], "takeoff")
                    messages.append(f"打击任务 {mission_guid} 创建后派出干扰机 {jammer['guid']}")
                except Exception as exc:
                    record["phase"] = "launch_uncertain"
                    messages.append(f"干扰机起飞结果未确认：{exc}")
                continue

            jammer = by_guid.get(_guid(record.get("jammer_guid")))
            if jammer is None:
                messages.append(f"干扰机 {record.get('jammer_guid')} 已不在己方态势，停止伴随")
                del bindings[mission_guid]
                continue
            created_sim = _scenario_time(record.get("created_at_sim"))
            waited_too_long = (created_sim and current_sim and not package["airborne"]
                               and (current_sim - created_sim).total_seconds() >= self.max_wait_sim_sec)
            # A legacy binding may have launched its jammer while every main
            # aircraft remained parked. Return the solo jammer immediately and
            # never enable its emitters or steering in that state.
            solo_jammer = (not package["airborne"] and
                           jammer.get("airborne") is True and
                           jammer.get("in_facility") is not True)
            if (current_sim and end_sim and current_sim >= end_sim) or waited_too_long or solo_jammer:
                try:
                    messages.append(self._finish(situation, mission_guid, record, jammer))
                    record["phase"] = "finished"
                except Exception as exc:
                    messages.append(f"干扰机结束伴随失败：{exc}")
                continue
            if not package["airborne"]:
                continue

            if jammer.get("in_facility") is True:
                if now - float(record.get("last_launch_at", 0)) >= self.launch_retry_sec:
                    record["last_launch_at"] = now
                    try:
                        self._script(situation, mission_guid, jammer["guid"], "takeoff")
                        messages.append(f"干扰机 {jammer['guid']} 尚未起飞，已重试出动")
                    except Exception as exc:
                        messages.append(f"干扰机重试出动失败：{exc}")
                continue
            if jammer.get("airborne") is not True:
                continue
            if not should_follow(record, package, now, interval_sec=self.follow_interval_sec):
                continue
            previous = record.get("last_striker_position") or record.get("launch_origin")
            previous = tuple(previous) if isinstance(previous, list) and len(previous) == 2 else None
            waypoint = (follow_waypoint(package, jammer, previous, self.trail_km)
                        if package["airborne"] else None)
            try:
                self._script(situation, mission_guid, jammer["guid"], "follow", waypoint=waypoint)
                record.update(phase="following", last_follow_at=now,
                              last_striker_position=[package["lat"], package["lon"]])
                messages.append(f"干扰机 {jammer['guid']} 开启干扰/雷达"
                                + (f"并伴随打击机群至 {waypoint}" if waypoint else "；等待主攻机移动"))
            except Exception as exc:
                record["phase"] = "follow_error"
                record["last_follow_at"] = now
                messages.append(f"干扰机伴随或设备控制失败：{exc}")
        return messages
