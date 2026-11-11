"""Allocate free, currently anti-ship-capable aircraft in groups of at least two.

The LLM chooses whether to launch a sea strike and orders priority targets.
This rule then distributes aircraft, using only current contacts and inventory.
No mission is sent here. One group becomes one task; simultaneous takeoff is
not guaranteed by this allocation (Mozi still controls readiness and departure).
"""

from copy import deepcopy

from rules.air_strike_review import _guid, _point, _distance_km, RADIUS_KM
from rules.awacs_area import is_awacs
from rules.jammer_support import is_jammer

SKILL = "air.sea_strike"
MIN_AIRCRAFT = 2


def _pool(situation, reserved=()):
    occupied = {_guid(x) for x in (situation.get("mission_assignments") or {})}
    occupied.update(_guid(x) for x in reserved)
    units = {}
    for row in situation.get("units", {}).get("air", []):
        if not isinstance(row, dict):
            continue
        guid = _guid(row.get("guid"))
        if (guid and guid not in occupied and row.get("unit_type") == "Aircraft"
                and not is_awacs(row) and not is_jammer(row)
                and "sea" in (row.get("strike_capabilities") or [])
                and row.get("weapon_info_status", "ok") == "ok"
                and _point(row) is not None):
            units[guid] = row
    return units


def allocation(situation, *, reserved=(), priority=()):
    """Balance quotas with augmenting-path matching, respecting EACH radius.

    First admit as many priority targets as can receive two aircraft. Then
    grow the smallest feasible group by one until no spare plane can join.
    Matching can move a flexible aircraft to preserve a geographically
    constrained aircraft's only target. Stable GUID ties make retries stable.
    """
    units = _pool(situation, reserved)
    covered = {_guid(x) for x in situation.get("active_air_sea_target_guids", [])}
    contacts, distances, edges = {}, {}, {}
    for row in situation.get("contacts", {}).get("sea", []):
        if not isinstance(row, dict):
            continue
        guid, point = _guid(row.get("guid")), _point(row)
        if not guid or guid in covered or point is None:
            continue
        eligible = {u: _distance_km(_point(unit), point) for u, unit in units.items()}
        eligible = {u: d for u, d in eligible.items() if d <= RADIUS_KM}
        if eligible:
            contacts[guid], distances[guid] = row, eligible
            edges[guid] = sorted(eligible, key=lambda u: (eligible[u], u))
    ranked = {guid: index for index, guid in enumerate(dict.fromkeys(map(_guid, priority)))}
    targets = sorted(contacts, key=lambda t: (
        ranked.get(t, len(ranked)), min(distances[t].values()), t))
    reachable = {u for target in targets for u in edges[target]}

    def match(quotas):
        slots = [(t, i) for t, count in quotas.items() for i in range(count)]
        owner = {}

        def place(slot, seen):
            for unit in edges[slot[0]]:
                if unit in seen:
                    continue
                seen.add(unit)
                if unit not in owner or place(owner[unit], seen):
                    owner[unit] = slot
                    return True
            return False

        for slot in sorted(slots, key=lambda s: (len(edges[s[0]]), targets.index(s[0]), s[1])):
            if not place(slot, set()):
                return None
        return owner

    quotas = {}
    for target in targets:
        trial = {**quotas, target: MIN_AIRCRAFT}
        if sum(trial.values()) <= len(reachable) and match(trial) is not None:
            quotas = trial
    while quotas and sum(quotas.values()) < len(reachable):
        grew = False
        for target in sorted(quotas, key=lambda t: (quotas[t], targets.index(t))):
            trial = {**quotas, target: quotas[target] + 1}
            if match(trial) is not None:
                quotas, grew = trial, True
                break
        if not grew:
            break
    assigned = match(quotas) or {}
    groups = [{"target_contact_guid": target,
               "target_name": str(contacts[target].get("name") or target)[:80],
               "unit_guids": sorted(u for u, slot in assigned.items() if slot[0] == target)}
              for target in quotas]
    return {"minimum_aircraft_per_target": MIN_AIRCRAFT,
            "available_aircraft": len(reachable), "eligible_targets": len(targets),
            "groups": groups, "waiting_unit_guids": sorted(set(units) - set(assigned)),
            "deferred_target_guids": [t for t in targets if t not in quotas],
            "policy": "整数均分；不足时集中部分目标，每目标至少2架，只有1架时待命；已有打击目标不重复建任务"}


def apply_allocation(calls, situation):
    """Replace proposed NEW sea-strike calls only after an explicit sea intent.

    Existing tasks and other proposed tasks keep their aircraft. All current
    eligible targets participate in N; proposed target order gives priority.
    Model output and deterministic adjustments are recorded separately.
    """
    sea = [c for c in calls if c.get("skill_id") == SKILL]
    if not sea:
        return deepcopy(calls), None
    current = {_guid(c.get("guid")) for c in situation.get("contacts", {}).get("sea", [])}
    current.discard("")
    original_sea, valid_sea, rejected = sea, [], []
    for call in sea:
        if call.get("existing_mission_guid"):
            raise ValueError("对海分组仅创建新任务，不能覆盖已有打击任务分配")
        targets = call.get("target_contact_guids")
        if not isinstance(targets, list) or not targets or any(
                _guid(t) not in current for t in targets):
            # A bad sea intent must not discard unrelated patrol/air tasks.
            # Never infer a replacement target from a name or historical ID.
            rejected.append({"call_id": call.get("call_id", ""),
                             "target_contact_guids": deepcopy(targets),
                             "reason": "缺少目标或目标不在当前水面接触；本项暂缓，其他合法调用继续"})
            continue
        valid_sea.append(call)
    sea = valid_sea
    other = [deepcopy(c) for c in calls if c.get("skill_id") != SKILL]
    reserved = [u for c in other for key in ("unit_guids", "escort_unit_guids")
                for u in c.get(key, [])]
    # Retain explicitly proposed escorts only on their original first target.
    reserved += [u for c in sea for u in c.get("escort_unit_guids", [])]
    priority = [t for c in sea for t in c.get("target_contact_guids", [])]
    report = allocation(situation, reserved=reserved, priority=priority)
    report["original_calls"] = deepcopy(original_sea)
    report["rejected_calls"] = rejected
    if not sea:
        # All sea intents were invalid. Do not turn the general current-target
        # pool into an implicit authorization to choose replacement targets.
        report["waiting_unit_guids"] = sorted(set(report["waiting_unit_guids"]).union(
            u for group in report["groups"] for u in group["unit_guids"]))
        report["deferred_target_guids"] = [g["target_contact_guid"] for g in report["groups"]] + report["deferred_target_guids"]
        report["groups"] = []
        return other, report
    used_ids = {c.get("call_id") for c in other}
    used_names = {c.get("mission_name") for c in other}
    used_names.update(m.get("name") for m in situation.get("existing_missions", []))
    used_escorts = set()
    for group in report["groups"]:
        target = group["target_contact_guid"]
        template = next((c for c in sea if _guid(c["target_contact_guids"][0]) == target), {})
        call_id = str(template.get("call_id") or f"sea-group-{target}")[:100]
        name = str(template.get("mission_name") or f"对海集中打击-{group['target_name']}")[:100]
        for key, taken in (("id", used_ids), ("name", used_names)):
            base = call_id if key == "id" else name
            candidate, index = base, 2
            while candidate in taken:
                suffix = f"-{index}"
                candidate, index = base[:100-len(suffix)] + suffix, index + 1
            taken.add(candidate)
            if key == "id":
                call_id = candidate
            else:
                name = candidate
        escorts = [u for u in template.get("escort_unit_guids", []) if _guid(u) not in used_escorts]
        used_escorts.update(map(_guid, escorts))
        other.append({"call_id": call_id, "skill_id": SKILL, "mission_name": name,
                      "unit_guids": group["unit_guids"], "target_contact_guids": [target],
                      "escort_unit_guids": escorts,
                      "reason": f"对海兵力分组规则：{len(group['unit_guids'])}架共同分配同一目标任务"})
    return other, report


def validate_groups(calls, situation):
    """Enforce minimum grouping at every Python entry point, including CLI."""
    units = {_guid(u.get("guid")): u for u in situation.get("units", {}).get("air", [])}
    contacts = {_guid(c.get("guid")): c for c in situation.get("contacts", {}).get("sea", [])}
    seen = set()
    covered = {_guid(x) for x in situation.get("active_air_sea_target_guids", [])}
    for call in calls:
        if call.skill_id != SKILL:
            continue
        if len({_guid(u) for u in call.unit_guids}) < MIN_AIRCRAFT:
            raise ValueError(f"{call.mission_name} 对海打击每个目标至少分配2架飞机；不足2架待命")
        if len(call.target_contact_guids) != 1:
            raise ValueError(f"{call.mission_name} 对海打击每项任务必须只对应一个目标")
        target = _guid(call.target_contact_guids[0])
        if target in seen or target in covered:
            raise ValueError(f"{call.mission_name} 对海目标已有打击任务，不能重复分散派机")
        seen.add(target)
        for guid in call.unit_guids:
            start, end = _point(units.get(_guid(guid), {})), _point(contacts.get(target, {}))
            if start is None or end is None or _distance_km(start, end) > RADIUS_KM:
                raise ValueError(f"{call.mission_name} 每架对海飞机到当前目标必须在1000公里内且位置已知")
