from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.planning.plan_adapter import normalize_plan
from runtime.skills.registry import SkillRegistry
from runtime.planning.validation import validate_plan


class PlanAdapterTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "examples" / "plan.multiple.example.json")
                               .read_text(encoding="utf-8"))
        self.situation = json.loads((ROOT / "examples" / "situation.example.json")
                                    .read_text(encoding="utf-8"))
        self.registry = SkillRegistry(ROOT / "skills").load()

    def test_valid_plan_is_unchanged_and_source_is_not_mutated(self):
        original = deepcopy(self.plan)
        self.assertEqual(normalize_plan(self.plan), original)
        self.assertEqual(self.plan, original)

    def test_known_variants_normalize_before_validation(self):
        call = deepcopy(self.plan["skill_calls"][0])
        call["unitGuids"] = call.pop("unit_guids")[0]
        call["area"] = call["area"]["points"]
        call["parameters"] = None
        plan = {"version": 1, "skill_calls": [call]}
        normalized = normalize_plan(plan)
        self.assertIsInstance(normalized["skill_calls"][0]["area"], dict)
        self.assertEqual(len(validate_plan(plan, self.situation, self.registry)), 1)
        self.assertIn("unitGuids", plan["skill_calls"][0])

    def test_conflicting_aliases_are_rejected(self):
        call = deepcopy(self.plan["skill_calls"][0])
        call["unitGuids"] = "bbbbbbbb-0000-0000-0000-000000000001"
        with self.assertRaisesRegex(ValueError, "冲突字段 unitGuids 和 unit_guids"):
            normalize_plan({"version": 1, "skill_calls": [call]})

    def test_observed_sea_target_guid_alias_reaches_validation(self):
        original = deepcopy(self.plan["skill_calls"][2])
        target = original["target_contact_guids"][0]
        for alias in ("target_guid", "target_guids", "target_contact_guid",
                      "targetContactGuid", "targetContactGuids"):
            with self.subTest(alias=alias):
                call = deepcopy(original)
                call.pop("target_contact_guids")
                call[alias] = target
                plan = {"version": 1, "skill_calls": [call]}
                normalized = normalize_plan(plan)["skill_calls"][0]
                self.assertEqual(normalized["target_contact_guids"], [target])
                self.assertNotIn(alias, normalized)
                self.assertIn(alias, call)
                validated = validate_plan(plan, self.situation, self.registry)
                self.assertEqual(validated[0].target_contact_guids, (target,))

    def test_equivalent_target_aliases_compare_as_lists(self):
        call = deepcopy(self.plan["skill_calls"][2])
        target = call["target_contact_guids"][0]
        call.update({"target_guid": target, "target_guids": [target],
                     "target_contact_guid": target, "targetContactGuid": target})
        normalized = normalize_plan({"version": 1, "skill_calls": [call]})["skill_calls"][0]
        self.assertEqual(normalized["target_contact_guids"], [target])
        self.assertTrue(all(key not in normalized for key in (
            "target_guid", "target_guids", "target_contact_guid", "targetContactGuid")))

    def test_conflicting_target_aliases_rejected_instead_of_overwritten(self):
        call = deepcopy(self.plan["skill_calls"][2])
        call["target_guid"] = "eeeeeeee-0000-0000-0000-000000000099"
        with self.assertRaisesRegex(ValueError, "冲突字段 target_guid 和 target_contact_guids"):
            normalize_plan({"version": 1, "skill_calls": [call]})

    def test_target_alias_does_not_resolve_names_or_stale_ids(self):
        for target in ("敌舰接触-1", "eeeeeeee-0000-0000-0000-000000000099"):
            with self.subTest(target=target):
                call = deepcopy(self.plan["skill_calls"][2])
                call.pop("target_contact_guids")
                call["target_guid"] = target
                with self.assertRaises(ValueError):
                    validate_plan({"version": 1, "skill_calls": [call]},
                                  self.situation, self.registry)

    def test_missing_strike_target_is_not_invented(self):
        call = deepcopy(self.plan["skill_calls"][2])
        call["target_contact_guids"] = []
        with self.assertRaisesRegex(ValueError, "必须选择蓝方当前接触"):
            validate_plan({"version": 1, "skill_calls": [call]},
                          self.situation, self.registry)


if __name__ == "__main__":
    unittest.main()
