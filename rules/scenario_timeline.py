"""把墨子想定时间变成联合指挥和军种 Agent 共用的简短时间提醒。

本项目的 ``scenario.start_time`` 实际是当前推演时间，``initial_time``
才是开局时间，``end_time`` 是截止时间。优先级只影响规划节奏：不能代替
实时目标/单位校验，也不禁止提前打击。稳定的推演时间段用于唤醒规划，
避免把每秒时钟变化都送给模型。
"""

from __future__ import annotations

from datetime import datetime


def _parse(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("T", " ")) if value else None
    except (TypeError, ValueError):
        return None


def scenario_timeline(scenario: dict | None) -> dict:
    scenario = scenario if isinstance(scenario, dict) else {}
    opening = _parse(scenario.get("initial_time"))
    current = _parse(scenario.get("start_time"))
    end = _parse(scenario.get("end_time"))
    result = {
        "total_minutes": None,
        "elapsed_minutes": None,
        "remaining_minutes": None,
        "elapsed_fraction": None,
        "planning_priority": "总时长未知",
        "planning_slot": None,
    }
    if current is None or end is None:
        return result
    try:
        result["remaining_minutes"] = round(max(0.0, (end - current).total_seconds()) / 60, 1)
        if opening is None:
            return result
        total = (end - opening).total_seconds()
        elapsed = min(total, max(0.0, (current - opening).total_seconds()))
    except TypeError:  # 混用有时区/无时区时间，不能可靠计算。
        return result
    if total <= 0:
        return result

    fraction = elapsed / total
    if fraction >= 1:
        priority = "想定已结束"
    elif fraction >= 0.9:
        priority = "临近结束"
    elif fraction >= 0.75:
        priority = "抓紧打击"
    elif fraction >= 0.5:
        priority = "打击优先"
    elif fraction >= 0.25:
        priority = "空域控制"
    else:
        priority = "侦察部署"
    # 按总时长划分规划时段，间隔限制在 5～15 推演分钟；暂停时不会反复唤醒。
    slot_seconds = min(900.0, max(300.0, total / 12))
    result.update(
        total_minutes=round(total / 60, 1),
        elapsed_minutes=round(elapsed / 60, 1),
        remaining_minutes=round(max(0.0, total - elapsed) / 60, 1),
        elapsed_fraction=round(fraction, 4),
        planning_priority=priority,
        planning_slot=int(elapsed // slot_seconds),
    )
    return result
