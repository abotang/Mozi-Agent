import unittest

from rules.air_allocation import fighter_allocation


class AirAllocationTests(unittest.TestCase):
    def test_assigned_parked_fighter_is_visible_but_not_free(self):
        state = {"units": {"air": [
            {"guid": "a", "name": "F-16A #3", "airborne": False, "in_facility": True},
            {"guid": "b", "name": "F-16A #5", "airborne": False, "in_facility": True}]},
            "mission_assignments": {"a": "m"},
            "existing_missions": [{"guid": "m", "name": "对海打击", "skill_id": "air.sea_strike"}]}
        result = fighter_allocation(state)
        self.assertEqual([u["name"] for u in result["available"]], ["F-16A #5"])
        self.assertEqual(result["assigned"][0]["mission_name"], "对海打击")
        self.assertFalse(result["assigned"][0]["airborne"])
