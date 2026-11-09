"""战术重规划触发：固定 GUID 的接触明显移动或跨越距离段时提前唤醒。

GUID 增删由自动循环原有签名判断；本模块只处理相同 GUID 的空间变化。
40 km 位移需至少隔 30 秒现实时间，避免高倍速推演每 10 秒请求模型。
进入/离开 300、150、75 km 距离段立即触发；预警机 300 km 回撤另由
awacs_triggers.py 判定，两者互不替代。
"""

from __future__ import annotations

from rules.situation_memory import _point, distance_km, nearest_blue_km, range_band


MOVE_KM = 40.0
MOVE_COOLDOWN_SEC = 30.0


def contact_snapshot(situation: dict) -> dict:
    result = {}
    for group, rows in (situation.get("contacts") or {}).items():
        for row in rows:
            if not isinstance(row, dict):
                continue
            guid = str(row.get("guid") or "").strip().strip("{}").lower()
            point = _point(row)
            if guid and point:
                result[guid] = {"point": point, "band": range_band(nearest_blue_km(point, situation)),
                                "group": group, "name": str(row.get("name") or guid)[:80]}
    return result


def tactical_refresh_reason(previous: dict | None, current: dict,
                            elapsed_sec: float) -> str:
    if not previous:
        return ""
    for guid, row in current.items():
        old = previous.get(guid)
        if not old:
            continue
        if (row["band"] != old["band"]
                and "unknown" not in (row["band"], old["band"])):
            return f"接触 {row['name']} 距蓝方进入新距离段：{old['band']} → {row['band']}"
        if elapsed_sec >= MOVE_COOLDOWN_SEC:
            moved = distance_km(old["point"], row["point"])
            if moved >= MOVE_KM:
                return f"接触 {row['name']} 自上次规划移动 {moved:.0f} km"
    return ""
