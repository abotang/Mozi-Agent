"""Current loadout role extraction and live strike compatibility checks."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from rules.air_weapon_roles import compatible_with_skill, read_current_loadout
from rules.air_strike_review import strike_opportunities
from runtime.planning.validation import validate_plan
from runtime.skills.registry import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]
AIM = {"name": "AIM-120C-7型先进中程空空导弹", "curNum": 2}
HARPOON = {"name": "AGM-84L型鱼叉-2反舰导弹", "curNum": 2}


def payload(weapons, *, code=0):
    return {"code": code, "data": [
        {"type": "mount", "name": "非挂载发射架",
         "weaponRecord": [HARPOON]},
        {"type": "loadout", "name": "测试挂载",
         "weaponRecord": weapons}]}


def read_mock(result):
    with patch("rules.air_weapon_roles.urlopen",
               return_value=BytesIO(json.dumps(result).encode("utf-8"))):
        return read_current_loadout("http://127.0.0.1:8050/ApiCtrl/api", "unit")


class AirWeaponRoleTests(unittest.TestCase):
    def test_positive_current_loadout_weapons_only(self):
        aim_only = read_mock(payload([AIM, {**HARPOON, "curNum": 0}]))
        self.assertEqual(aim_only["strike_capabilities"], ["air"])
        self.assertEqual(aim_only["loadout_name"], "测试挂载")
        self.assertEqual(aim_only["weapon_info_status"], "ok")
        mixed = read_mock(payload([AIM, HARPOON]))
        self.assertEqual(mixed["strike_capabilities"], ["air", "sea"])
        self.assertTrue(compatible_with_skill(mixed, "air.air_strike", live=True))
        self.assertTrue(compatible_with_skill(mixed, "air.sea_strike", live=True))

    def test_failed_or_malformed_read_is_unknown_and_not_strike_capable(self):
        self.assertEqual(read_mock(payload([AIM], code=101))["weapon_info_status"], "unknown")
        self.assertEqual(read_mock(payload([{"name": "反舰导弹", "curNum": "bad"}]))
                         ["strike_capabilities"], [])
        with patch("rules.air_weapon_roles.urlopen", side_effect=OSError("offline")):
            unknown = read_current_loadout("http://127.0.0.1:8050/ApiCtrl/api", "unit")
        self.assertFalse(compatible_with_skill(unknown, "air.sea_strike", live=True))
        self.assertTrue(compatible_with_skill({}, "air.sea_strike", live=False))
        self.assertFalse(compatible_with_skill({}, "air.sea_strike", live=True))

    def test_live_sea_strike_rejects_aim_only_but_accepts_mixed(self):
        situation = json.loads((ROOT / "examples/situation.example.json").read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "examples/plan.multiple.example.json").read_text(encoding="utf-8"))
        call = plan["skill_calls"][2]
        selected = next(row for row in situation["units"]["air"]
                        if row["guid"] == call["unit_guids"][0])
        registry = SkillRegistry(ROOT / "skills").load()
        situation["weapon_inventory_live"] = True
        selected.update(strike_capabilities=["air"], weapon_info_status="ok")
        with self.assertRaisesRegex(ValueError, "当前挂载无可用"):
            validate_plan({"version": 1, "skill_calls": [call]}, situation, registry)
        # The fixed PowerShell script swaps the requested loadout, then reads
        # the actual mounted weapons before creating the mission.
        rearmed = {**call, "loadout_guid": "12345678-1234-4234-8234-123456789012"}
        self.assertEqual(len(validate_plan(
            {"version": 1, "skill_calls": [rearmed]}, situation, registry)), 1)
        selected["strike_capabilities"] = ["air", "sea"]
        self.assertEqual(len(validate_plan({"version": 1, "skill_calls": [call]},
                                           situation, registry)), 1)
        del selected["strike_capabilities"]
        with self.assertRaisesRegex(ValueError, "当前挂载无可用"):
            validate_plan({"version": 1, "skill_calls": [call]}, situation, registry)
        situation.pop("weapon_inventory_live")
        self.assertEqual(len(validate_plan({"version": 1, "skill_calls": [call]},
                                           situation, registry)), 1)

    def test_live_opportunities_exclude_incompatible_or_unknown_aircraft(self):
        fighter = {"guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                   "name": "F-16", "unit_type": "Aircraft",
                   "lat": 0.0, "lon": 0.0,
                   "strike_capabilities": ["air"]}
        state = {"weapon_inventory_live": True, "mission_assignments": {},
                 "contacts": {"air": [{"guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                                       "lat": 0.0, "lon": 1.0}],
                              "sea": [{"guid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                                       "lat": 0.0, "lon": 1.0}],
                              "land": []}}
        skills = [{"skill_id": "air.air_strike"}, {"skill_id": "air.sea_strike"}]
        result = strike_opportunities(state, skills, [fighter])["eligible_by_skill"]
        self.assertEqual(len(result["air.air_strike"]), 1)
        self.assertEqual(result["air.sea_strike"], [])
        fighter["strike_capabilities"] = ["air", "sea"]
        result = strike_opportunities(state, skills, [fighter])["eligible_by_skill"]
        self.assertEqual(len(result["air.sea_strike"]), 1)
        fighter.pop("strike_capabilities")
        result = strike_opportunities(state, skills, [fighter])["eligible_by_skill"]
        self.assertEqual(result["air.air_strike"], [])
        self.assertEqual(result["air.sea_strike"], [])


if __name__ == "__main__":
    unittest.main()

