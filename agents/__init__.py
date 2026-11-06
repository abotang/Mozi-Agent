"""五个明确的 LLM 指挥 Agent。"""

from .roles import AirCommanderAgent, ArmyCommanderAgent, AwacsCommanderAgent, JointCommanderAgent, NavyCommanderAgent

__all__ = ["JointCommanderAgent", "AirCommanderAgent", "AwacsCommanderAgent",
           "NavyCommanderAgent", "ArmyCommanderAgent"]
