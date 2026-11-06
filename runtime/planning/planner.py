"""联合指挥 + 空军/预警机/海军/陆军 Agent：只输出任务 Skill 调用。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from agents.model_client import AgentConfig, ChatCompletionClient
from runtime.planning.plan_adapter import normalize_plan

from runtime.contracts import AGENT_OWNERS, SERVICES
from runtime.skills.registry import SkillRegistry
from agents import (AirCommanderAgent, ArmyCommanderAgent, AwacsCommanderAgent,
                    JointCommanderAgent, NavyCommanderAgent)
from agents.prompt_loader import load_prompt
from rules.awacs_area import is_awacs
from rules.awacs_deployment import minimum_separation_km
from rules.navy_patrol import available_skills


class HierarchicalSkillPlanner:
    def __init__(self, config: AgentConfig, registry: SkillRegistry,
                 client: ChatCompletionClient | None = None):
        self.config = config
        self.registry = registry
        self.client = client or ChatCompletionClient(config)
        self.joint_agent = JointCommanderAgent(self.client, load_prompt("joint"))
        self.service_agents = {
            "air": AirCommanderAgent(self.client, load_prompt("air")),
            "awacs": AwacsCommanderAgent(self.client, load_prompt("awacs")),
            "navy": NavyCommanderAgent(self.client, load_prompt("navy")),
            "army": ArmyCommanderAgent(self.client, load_prompt("army")),
        }

    @classmethod
    def from_config(cls, path, registry: SkillRegistry):
        return cls(AgentConfig.load(path), registry)

    @staticmethod
    def _joint(raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("联合指挥 Agent 输出必须为 version=1 的对象")
        intents = raw.get("mission_intents")
        if not isinstance(intents, list) or len(intents) > 30:
            raise ValueError("联合指挥 mission_intents 必须是最多30项的数组")
        for item in intents:
            if not isinstance(item, dict) or item.get("service") not in AGENT_OWNERS:
                raise ValueError("联合指挥任务意图必须归属 air/awacs/navy/army")
            if any(key in item for key in ("unit_id", "unit_guids", "area", "skill_calls")):
                raise ValueError("联合指挥不得选择具体单位、区域或 Skill 调用")
        return raw

    @staticmethod
    def _component(raw: dict[str, Any], service: str) -> dict[str, Any]:
        if (not isinstance(raw, dict) or raw.get("version") != 1
                or raw.get("service") != service):
            raise ValueError(f"{service} Agent 输出协议无效")
        calls = raw.get("skill_calls")
        if not isinstance(calls, list) or len(calls) > 100:
            raise ValueError(f"{service} Agent skill_calls 必须是最多100项的数组")
        for index, call in enumerate(calls, 1):
            if not isinstance(call, dict):
                raise ValueError(f"{service} Agent skill_calls 第 {index} 项必须是 JSON 对象")
        return raw

    def plan(self, situation: dict[str, Any], instruction: str) -> dict[str, Any]:
        rules = dict(situation.get("operational_rules") or {})
        rules["awacs_min_separation_km"] = minimum_separation_km(situation)
        navy_patrol_allowed = rules.get("navy_patrol_opening_allowed", True)

        def catalog(owner: str) -> list[dict]:
            entries = self.registry.public_catalog_for_agent(owner)
            return available_skills(owner, entries, navy_patrol_allowed)

        aircraft = situation["units"].get("air", [])
        own_unit_rows = {
            "air": [row for row in aircraft if not is_awacs(row)],
            "awacs": [row for row in aircraft if is_awacs(row)],
            "navy": situation["units"].get("navy", []),
            "army": situation["units"].get("army", []),
        }

        joint_context = {
            "objective": self.config.objective,
            "instruction": instruction,
            "situation": deepcopy(situation),
            "available_task_skills": {
                owner: catalog(owner) for owner in AGENT_OWNERS
            },
        }
        directive = self._joint(self.joint_agent.decide(joint_context))

        def decide(owner: str) -> dict[str, Any]:
            if owner == "awacs" and (not own_unit_rows[owner] or
                                     rules.get("awacs_trigger", {}).get("kind") == "hold"):
                return {"version": 1, "service": owner,
                        "summary": "预警机规则未触发或无可用预警机", "skill_calls": []}
            context = {
                "objective": self.config.objective,
                "instruction": instruction,
                "joint_directive": directive,
                "operational_rules": deepcopy(rules),
                "own_units": deepcopy(own_unit_rows[owner]),
                "friendly_context": {
                    key: deepcopy(value) for key, value in situation["units"].items()
                    if key in SERVICES and key != ("air" if owner == "awacs" else owner)
                },
                "contacts": deepcopy(situation["contacts"]),
                "existing_missions": deepcopy(situation.get("existing_missions", [])),
                "existing_areas": deepcopy(situation.get("existing_areas", [])),
                "task_skills": catalog(owner),
            }
            proposal = self._component(self.service_agents[owner].decide(context), owner)
            proposal["skill_calls"] = normalize_plan({
                "version": 1, "skill_calls": proposal["skill_calls"]
            })["skill_calls"]
            return proposal

        with ThreadPoolExecutor(max_workers=self.config.component_parallelism,
                                thread_name_prefix="skill-planner") as pool:
            proposals = dict(zip(AGENT_OWNERS, pool.map(decide, AGENT_OWNERS)))
        for owner, proposal in proposals.items():
            own_ids = {
                str(item.get("guid", item.get("id", ""))).strip().strip("{}").lower()
                for item in own_unit_rows[owner] if isinstance(item, dict)
            }
            for call in proposal["skill_calls"]:
                spec = self.registry.get(str(call.get("skill_id", "")))
                if spec.agent_owner != owner:
                    raise ValueError(f"{owner} Agent 越权调用了 {spec.agent_owner} Skill：{spec.skill_id}")
                selected = call.get("unit_guids") or []
                if any(str(guid).strip().strip("{}").lower() not in own_ids for guid in selected):
                    raise ValueError(f"{owner} Agent 使用了不属于 own_units 的主任务单元")
        skill_calls = [call for owner in AGENT_OWNERS for call in proposals[owner]["skill_calls"]]
        return {
            "version": 1,
            "summary": "；".join(
                str(proposals[owner].get("summary", "")).strip()
                for owner in AGENT_OWNERS if str(proposals[owner].get("summary", "")).strip()
            ),
            "joint_directive": directive,
            "service_proposals": proposals,
            "skill_calls": skill_calls,
        }
