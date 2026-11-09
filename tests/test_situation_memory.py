"""态势记忆和同 GUID 位移触发的离线规则测试。"""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rules.joint_refresh import refresh_reason, strategic_snapshot
from rules.situation_memory import (filter_brief, memory_brief, record_dispatch_outcome,
                                    record_plan, update_memory)
from rules.situation_refresh import contact_snapshot, tactical_refresh_reason


def situation(when="2026-06-05 08:00:00", lon=4.0):
    return {
        "scenario": {"name": "测试想定", "initial_time": "2026-06-05 08:00:00",
                     "start_time": when},
        "side": {"guid": "blue"},
        "units": {"air": [], "navy": [{"guid": "ship", "lat": 0, "lon": 0}],
                  "army": [], "submarine": []},
        "contacts": {"air": [{"guid": "enemy-1", "name": "敌机", "lat": 0, "lon": lon}],
                     "sea": [], "land": [], "submarine": []},
    }


class SituationMemoryTests(unittest.TestCase):
    def test_tracks_approach_and_survives_json_restart(self):
        memory = update_memory({}, situation(lon=4.0))
        memory = update_memory(memory, situation(when="2026-06-05 08:05:00", lon=3.5))
        memory = json.loads(json.dumps(memory))
        trend = memory_brief(memory)["active_contact_trends"][0]
        self.assertGreater(trend["approach_km"], 50)
        self.assertGreater(trend["approach_kmh"], 0)
        self.assertEqual(trend["course_deg"], 270)
        self.assertEqual(len(filter_brief(memory_brief(memory), "navy")["active_contact_trends"]), 1)

    def test_lost_contact_is_history_only_and_rewind_clears_old_game(self):
        memory = update_memory({}, situation())
        empty = situation(when="2026-06-05 08:01:00")
        empty["contacts"]["air"] = []
        memory = update_memory(memory, empty)
        brief = memory_brief(memory)
        self.assertEqual(brief["active_contact_trends"], [])
        self.assertEqual(brief["recent_events"][-1]["type"], "lost_contact")
        memory = update_memory(memory, situation(when="2026-06-05 07:59:00"))
        self.assertEqual(len(memory["tracks"]), 1)
        self.assertEqual([item["type"] for item in memory["events"]], ["new_contact"])

    def test_plan_history_is_bounded_and_labelled_not_execution(self):
        memory = update_memory({}, situation())
        for index in range(10):
            record_plan(memory, {"joint_directive": {"main_effort": f"方向 {index}"},
                                 "service_proposals": {"air": {"summary": "拦截"}},
                                 "skill_calls": [{"skill_id": "air.air_strike",
                                                  "mission_name": f"任务 {index}"}]},
                        f"2026-06-05 08:{index:02d}:00")
        self.assertEqual(len(memory["plans"]), 6)
        brief = memory_brief(memory)
        self.assertEqual(len(brief["recent_plans_not_execution_results"]), 3)
        self.assertEqual(brief["recent_plans_not_execution_results"][-1]["joint_effort"], "方向 9")

    def test_fixed_guid_movement_and_range_crossing_trigger(self):
        baseline = contact_snapshot(situation(lon=4.0))
        moved = contact_snapshot(situation(lon=3.5))
        self.assertEqual(tactical_refresh_reason(baseline, moved, 20), "")
        self.assertIn("移动", tactical_refresh_reason(baseline, moved, 31))
        outside = contact_snapshot(situation(lon=2.75))
        inside = contact_snapshot(situation(lon=2.68))
        self.assertIn("距离段", tactical_refresh_reason(outside, inside, 5))
        previous = strategic_snapshot(situation(lon=2.75))
        current = strategic_snapshot(situation(lon=2.68))
        cache = {"instruction": "防御", "snapshot": previous, "at": 100,
                 "directive": {"version": 1}}
        self.assertEqual(refresh_reason(cache, current, "防御", 110, 900),
                         "敌方威胁距离段变化")

    def test_memory_view_has_fixed_contact_limit(self):
        current = situation()
        current["contacts"]["air"] = [
            {"guid": f"enemy-{index}", "name": f"敌机 {index}", "lat": 0, "lon": 4 + index / 100}
            for index in range(100)]
        memory = update_memory({}, current)
        self.assertLessEqual(len(memory["tracks"]), 80)
        self.assertLessEqual(len(memory_brief(memory)["active_contact_trends"]), 12)
        self.assertLessEqual(len(memory_brief(memory, "air")["active_contact_trends"]), 7)
        self.assertLess(len(json.dumps(memory_brief(memory), ensure_ascii=False)), 12000)

    def test_dispatch_results_are_separate_from_plans_and_combat_effects(self):
        memory = update_memory({}, situation())
        record_dispatch_outcome(memory, "2026-06-05 08:01:00", "空中巡逻", "air.combat_patrol",
                                "created", "mission-guid")
        record_dispatch_outcome(memory, "2026-06-05 08:02:00", "海上打击", "navy.sea_strike",
                                "uncertain", "目标未回读")
        brief = memory_brief(memory)
        self.assertEqual(brief["recent_plans_not_execution_results"], [])
        self.assertEqual([row["status"] for row in brief["recent_task_dispatch_outcomes_not_combat_effects"]],
                         ["created", "uncertain"])
        self.assertEqual(len(filter_brief(brief, "air")["recent_task_dispatch_outcomes_not_combat_effects"]), 1)


if __name__ == "__main__":
    unittest.main()
