"""联合指挥、空军、预警机、海军和陆军 Agent 的模型调用边界。"""

from __future__ import annotations
from typing import Any


class JointCommanderAgent:
    role_key = "joint"
    display_name = "联合指挥 Agent"

    def __init__(self, client, prompt: str):
        self.client = client
        self.prompt = prompt

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.client.plan(self.role_key, self.prompt, context)


class ServiceCommanderAgent:
    service = ""
    display_name = ""

    def __init__(self, client, prompt: str):
        self.client = client
        self.prompt = prompt

    @property
    def role_key(self) -> str:
        return f"component_{self.service}"

    def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.client.plan(self.role_key, self.prompt, context)


class AirCommanderAgent(ServiceCommanderAgent):
    service = "air"
    display_name = "空军"


class AwacsCommanderAgent(ServiceCommanderAgent):
    service = "awacs"
    display_name = "预警机"


class NavyCommanderAgent(ServiceCommanderAgent):
    service = "navy"
    display_name = "海军"


class ArmyCommanderAgent(ServiceCommanderAgent):
    service = "army"
    display_name = "陆军"
