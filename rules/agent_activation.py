"""Decide whether a service Agent has any executable task capability."""

from __future__ import annotations


CONTACT_GROUP = {"Aircraft": "air", "Ship": "sea", "Facility": "land",
                 "Submarine": "submarine"}


def skip_reason(owner: str, own_units: list[dict], skills: list[dict],
                contacts: dict, operational_rules: dict) -> str:
    if not own_units:
        return "没有本 Agent 可控制的蓝方单元"
    if not skills:
        return "没有当前可调用的任务 Skill"
    if owner == "awacs" and operational_rules.get("awacs_trigger", {}).get("kind") == "hold":
        return "预警机触发规则未唤醒"
    # A patrol/support Skill can be useful without an enemy contact. A force
    # with strike-only Skills cannot create any valid task without a target.
    if all(skill.get("requires_target") is True for skill in skills):
        if not any(contacts.get(CONTACT_GROUP.get(skill.get("target_kind"), ""), [])
                   for skill in skills):
            return "当前 Skill 均需打击目标，但没有对应类别的实时接触"
    return ""
