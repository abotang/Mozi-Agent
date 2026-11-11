import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.scenario_timeline import scenario_timeline
from runtime.app.daemon import _signature


class ScenarioTimelineTests(unittest.TestCase):
    def test_three_hour_twenty_minute_scenario_uses_simulation_clock(self):
        budget = scenario_timeline({
            "initial_time": "2021-05-26 02:55:43",
            "start_time": "2021-05-26 04:41:45",
            "end_time": "2021-05-26 06:15:43",
        })
        self.assertEqual(budget["total_minutes"], 200.0)
        self.assertEqual(budget["elapsed_minutes"], 106.0)
        self.assertEqual(budget["remaining_minutes"], 94.0)
        self.assertAlmostEqual(budget["elapsed_fraction"], 0.5302)
        self.assertEqual(budget["planning_priority"], "打击优先")
        self.assertEqual(budget["planning_slot"], 7)

    def test_missing_opening_time_never_invents_total_duration(self):
        budget = scenario_timeline({
            "start_time": "2021-05-26 04:00:00",
            "end_time": "2021-05-26 06:00:00",
        })
        self.assertIsNone(budget["total_minutes"])
        self.assertIsNone(budget["elapsed_fraction"])
        self.assertEqual(budget["remaining_minutes"], 120.0)
        self.assertIsNone(budget["planning_slot"])

    def test_planning_signature_changes_on_simulated_slot_not_each_tick(self):
        situation = json.loads((ROOT / "examples/situation.example.json")
                               .read_text(encoding="utf-8"))
        situation["scenario"].update(
            initial_time="2026-11-06 10:00:00",
            end_time="2026-11-06 13:20:00",
            start_time="2026-11-06 10:00:00",
        )
        first = _signature(situation)
        situation["scenario"]["start_time"] = "2026-11-06 10:10:00"
        self.assertEqual(_signature(situation), first)
        situation["scenario"]["start_time"] = "2026-11-06 10:15:00"
        self.assertNotEqual(_signature(situation), first)


if __name__ == "__main__":
    unittest.main()
