from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.prompt_loader import load_prompt
from agents.model_client import AgentConfig
from runtime.planning.planner import HierarchicalSkillPlanner
from runtime.skills.registry import SkillRegistry


class PromptLoadingTests(unittest.TestCase):
    def test_service_prompts_are_short_and_service_specific(self):
        air = load_prompt("air")
        awacs = load_prompt("awacs")
        navy = load_prompt("navy")
        army = load_prompt("army")
        self.assertLess(len(air), 750)
        self.assertLess(len(navy), 750)
        self.assertLess(len(army), 350)
        self.assertLess(len(awacs), 1100)
        self.assertNotIn("air.early_warning", air)
        self.assertIn("air.early_warning", awacs)
        self.assertNotIn("对海打击", awacs)
        self.assertNotIn("navy.sea_patrol", air)
        self.assertNotIn("舰艇海上巡逻", air)
        self.assertIn("navy.sea_patrol", navy)
        self.assertNotIn("空中预警", navy)
        self.assertNotIn("舰艇", army)
        self.assertIn("skill_calls 必须为 []", army)

    def test_each_agent_loads_its_own_editable_prompt_file(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            directory = Path(folder)
            for role in ("joint", "air", "awacs", "navy", "army"):
                (directory / f"{role}.md").write_text(f"marker for {role}", encoding="utf-8")
            with patch("agents.prompt_loader.PROMPTS", directory):
                config = AgentConfig(base_url="http://127.0.0.1:1/v1", model="fake",
                                     objective="test")
                planner = HierarchicalSkillPlanner(
                    config, SkillRegistry(ROOT / "skills").load(), client=object())
                self.assertEqual(planner.joint_agent.prompt, "marker for joint")
                for role, agent in planner.service_agents.items():
                    self.assertEqual(agent.prompt, f"marker for {role}")
                self.assertEqual(load_prompt("navy"), "marker for navy")

    def test_missing_prompt_fails_before_model_request(self):
        with TemporaryDirectory(dir=ROOT) as folder:
            with patch("agents.prompt_loader.PROMPTS", Path(folder)):
                with self.assertRaises(FileNotFoundError):
                    load_prompt("joint")


if __name__ == "__main__":
    unittest.main()
